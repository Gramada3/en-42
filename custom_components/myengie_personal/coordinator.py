"""Coordinator: aduce toate datele contului MyENGIE într-o structură normalizată."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import MyEngieApi, MyEngieAuthError, MyEngieError, MyEngieMaintenance
from .const import CONF_TOKEN_DATA, DIVISIONS, DOMAIN, HEAVY_EVERY

_LOGGER = logging.getLogger(__name__)


# ── utilitare de normalizare ──────────────────────────────────

def _flatten_dicts_with(obj: Any, key: str) -> list[dict]:
    """Găsește recursiv toate elementele din obj[key] (liste de dict-uri)."""
    out: list[dict] = []
    if isinstance(obj, dict):
        if isinstance(obj.get(key), list):
            out.extend(x for x in obj[key] if isinstance(x, dict))
        else:
            for value in obj.values():
                if isinstance(value, (dict, list)):
                    out.extend(_flatten_dicts_with(value, key))
    elif isinstance(obj, list):
        for item in obj:
            out.extend(_flatten_dicts_with(item, key))
    return out


def _flatten_invoices(raw: Any) -> list[dict]:
    if isinstance(raw, dict):
        raw = raw.get("invoices", [raw])
    result: list[dict] = []
    for item in raw or []:
        if isinstance(item, dict) and isinstance(item.get("invoices"), list):
            result.extend(i for i in item["invoices"] if isinstance(i, dict))
        elif isinstance(item, dict):
            result.append(item)
    return result


def _as_dict_list(raw: Any, *keys: str) -> list[dict]:
    if isinstance(raw, dict):
        for key in keys:
            if isinstance(raw.get(key), list):
                raw = raw[key]
                break
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    return []


def _unread_count(raw: Any) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw)
    if isinstance(raw, dict):
        for key in ("unread_number", "unread", "number", "count", "total"):
            if key in raw:
                return _unread_count(raw[key])
    return None


def _three_years_ago() -> date:
    today = date.today()
    try:
        start = today.replace(year=today.year - 3)
    except ValueError:
        start = today.replace(year=today.year - 3, day=28)
    return start + timedelta(days=1)


class MyEngieCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Un coordinator per cont."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: MyEngieApi, interval: int) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{entry.entry_id[:6]}",
            update_interval=timedelta(seconds=interval),
        )
        self.api = api
        self._cycle = 0

    # ── helpers ────────────────────────────────────────────────

    async def _safe(self, label: str, coro, default: Any = None) -> Any:
        try:
            return await coro
        except (MyEngieAuthError, MyEngieMaintenance):
            raise
        except MyEngieError as err:
            _LOGGER.debug("%s indisponibil: %s", label, err)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Eroare neașteptată la %s", label)
        return default

    def _persist_tokens(self) -> None:
        tokens = self.api.export_tokens()
        if tokens and self.config_entry.data.get(CONF_TOKEN_DATA) != tokens:
            self.hass.config_entries.async_update_entry(
                self.config_entry, data={**self.config_entry.data, CONF_TOKEN_DATA: tokens}
            )

    # ── per loc de consum ───────────────────────────────────────

    async def _fetch_poc(self, raw_poc: dict, heavy: bool, prev: dict) -> dict:
        poc = str(raw_poc.get("poc_number", "")).strip()
        pa = str(raw_poc.get("pa", "")).strip()
        api = self.api

        div_data = await self._safe(f"divizii {poc}", api.divisions(poc, pa), {}) or {}
        details = div_data.get("details") if isinstance(div_data.get("details"), dict) else {}

        installations: dict[str, dict] = {}
        for div in DIVISIONS:
            for inst in div_data.get(div) or []:
                inst = str(inst)
                meta = next(
                    (d for d in details.values() if isinstance(d, dict) and str(d.get("installation_number")) == inst),
                    {},
                )
                installations[inst] = {
                    "division": div,
                    "installation": inst,
                    "pod": meta.get("pod", ""),
                    "serial": str(meta.get("serie_contor") or meta.get("serial_number") or meta.get("meterSerial") or ""),
                    "contract_account": meta.get("contract_account_number"),
                    "meta": meta,
                }

        # Index curent + prognoză (paralel)
        async def _index_and_prog(inst: dict) -> None:
            reading_raw, prog = await asyncio.gather(
                self._safe("index", api.index(poc, inst["division"], pa, inst["installation"])),
                self._safe("prognoză", api.prognosis(poc, inst["installation"], pa)),
            )
            reading = next(
                (r for r in _flatten_dicts_with(reading_raw, "installations")
                 if str(r.get("installation_number")) == inst["installation"]),
                None,
            )
            if reading is None:
                found = _flatten_dicts_with(reading_raw, "installations")
                reading = found[0] if len(found) == 1 else {}
            inst["reading"] = reading
            inst["prognosis"] = prog if isinstance(prog, dict) else {}
            if not inst["serial"]:
                inst["serial"] = str(reading.get("serie_contor") or "")
            if not inst["pod"]:
                inst["pod"] = reading.get("pod", "")

        await asyncio.gather(*(_index_and_prog(i) for i in installations.values()))

        # Istoric citiri (depinde de autocit din index)
        start_hist = _three_years_ago()

        async def _history(inst: dict) -> None:
            autocit = str(inst.get("reading", {}).get("autocit") or "")
            raw = await self._safe("istoric index", api.index_history(poc, inst["division"], autocit, start_hist))
            inst["history"] = _as_dict_list(raw, "istoric_citiri")

        await asyncio.gather(*(_history(i) for i in installations.values()))

        # Conturi contract pentru sold
        accounts: list[int] = []
        for cc in raw_poc.get("cont_contract") or []:
            if isinstance(cc, dict) and str(cc.get("contract_account_number", "")).isdigit():
                accounts.append(int(cc["contract_account_number"]))
        if not accounts:
            for inst in installations.values():
                ca = str(inst.get("contract_account") or "")
                if ca.isdigit() and int(ca) not in accounts:
                    accounts.append(int(ca))

        today = date.today()
        graph_start = date(today.year - 1, today.month, 1)
        pods = sorted({i["pod"] for i in installations.values() if i["pod"]})

        tasks: dict[str, Any] = {
            "invoices": api.invoices(poc, pa),
            "consumption": api.consumption(poc, pa, graph_start, today),
        }
        if accounts:
            tasks["balance"] = api.balance(accounts)
        if heavy or not prev:
            tasks["payments"] = api.payments(pa, date(today.year - 1, 1, 1), today)
            tasks["green_bill"] = api.green_bill(poc, pa)
            tasks["distributor"] = api.distributor(poc)
        for pod in pods:
            tasks[f"insp:{pod}"] = api.inspection(poc, pod, pa)
            if heavy or not prev:
                tasks[f"tech:{pod}"] = api.technical_services(poc, pod, pa)

        keys = list(tasks)
        values = await asyncio.gather(*(self._safe(k, tasks[k]) for k in keys))
        res = dict(zip(keys, values))

        def _keep(key: str, value: Any, default: Any) -> Any:
            if key in res and value is not None:
                return value
            return prev.get(key, default)

        inspection = {pod: res.get(f"insp:{pod}") or {} for pod in pods}
        technical = {
            pod: res[f"tech:{pod}"] if f"tech:{pod}" in res else prev.get("technical", {}).get(pod)
            for pod in pods
        }

        return {
            "poc": poc,
            "pa": pa,
            "address": div_data.get("address") or raw_poc.get("address") or {},
            "raw": raw_poc,
            "contract_accounts": accounts,
            "installations": installations,
            "divisions": sorted({i["division"] for i in installations.values()}),
            "balance": res.get("balance") or {},
            "invoices": _flatten_invoices(res.get("invoices")),
            "consumption": _as_dict_list(res.get("consumption"), "consumption", "items"),
            "payments": _as_dict_list(_keep("payments", res.get("payments"), []), "payments"),
            "green_bill": _keep("green_bill", res.get("green_bill"), {}),
            "distributor": _keep("distributor", res.get("distributor"), {}),
            "inspection": inspection,
            "technical": technical,
        }

    # ── update principal ───────────────────────────────────────

    async def _async_update_data(self) -> dict[str, Any]:
        heavy = self._cycle % HEAVY_EVERY == 0
        prev_data = self.data or {}
        try:
            await self.api.ensure_auth()
            places, profile, contracts, partners = await asyncio.gather(
                self.api.places_of_consumption(),
                self._safe("profil", self.api.user_profile(), {}),
                self._safe("contracte", self.api.contracts(), []),
                self._safe("titulari", self.api.partner_details(), []),
            )
            if heavy or "notifications" not in prev_data:
                unread_raw, notif_raw = await asyncio.gather(
                    self._safe("notificări necitite", self.api.notifications_unread()),
                    self._safe("notificări", self.api.notifications()),
                )
                unread = _unread_count(unread_raw)
                notifications = _as_dict_list(notif_raw, "notifications", "items")
            else:
                unread = prev_data.get("unread")
                notifications = prev_data.get("notifications", [])

            prev_pocs = prev_data.get("pocs", {})
            valid = [p for p in places if p.get("poc_number") and p.get("pa")]
            results = await asyncio.gather(
                *(self._fetch_poc(p, heavy, prev_pocs.get(str(p["poc_number"]).strip(), {})) for p in valid),
                return_exceptions=True,
            )
        except MyEngieAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except MyEngieMaintenance as err:
            raise UpdateFailed("Serverul ENGIE este în mentenanță") from err
        except MyEngieError as err:
            raise UpdateFailed(str(err)) from err

        pocs: dict[str, dict] = {}
        for raw_poc, result in zip(valid, results):
            key = str(raw_poc["poc_number"]).strip()
            if isinstance(result, ConfigEntryAuthFailed | MyEngieAuthError):
                raise ConfigEntryAuthFailed(str(result))
            if isinstance(result, Exception):
                _LOGGER.warning("Loc de consum %s: %s", key, result)
                if key in prev_pocs:
                    pocs[key] = prev_pocs[key]
                continue
            pocs[key] = result

        self._cycle += 1
        self._persist_tokens()
        return {
            "pocs": pocs,
            "profile": profile or {},
            "contracts": contracts or [],
            "partners": partners or [],
            "unread": unread,
            "notifications": notifications,
        }

    # ── acțiuni ────────────────────────────────────────────────

    def find_installation(self, installation: str) -> tuple[dict, dict] | None:
        for poc in (self.data or {}).get("pocs", {}).values():
            inst = poc["installations"].get(str(installation))
            if inst:
                return poc, inst
        return None

    async def async_submit_index(self, installation: str, value: int) -> Any:
        found = self.find_installation(installation)
        if not found:
            raise MyEngieError(f"Instalația {installation} nu există în cont")
        poc, inst = found
        try:
            result = await self.api.submit_index(
                poc["poc"], poc["pa"], inst["division"], inst["installation"], int(value), inst["serial"]
            )
        except MyEngieAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        if isinstance(result, dict) and result.get("error") is True:
            raise MyEngieError(f"ENGIE a respins indexul: {result.get('errors') or result.get('message')}")
        await self.async_request_refresh()
        return result
