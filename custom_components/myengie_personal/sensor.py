"""Senzori MyENGIE (personal)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MyEngieConfigEntry
from .const import ATTRIBUTION, DOMAIN, MONTHS_RO, NAME
from .coordinator import MyEngieCoordinator
from .entity import MyEngieEntity

_MONTHS_EN = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
]


# ── formatare ───────────────────────────────────────────────────

def parse_date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value).strip()[:19]
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m"):
        try:
            return datetime.strptime(text if "T" in fmt or len(text) <= 10 else text[:10], fmt).date()
        except ValueError:
            continue
    return None


def ro_date(value: Any) -> str:
    d = parse_date(value)
    return f"{d.day} {MONTHS_RO[d.month - 1]} {d.year}" if d else (str(value) if value else "—")


def ron(value: Any) -> str:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{num:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " lei"


def to_float(value: Any) -> float:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def _invoices_for(poc: dict, division: str) -> list[dict]:
    return [i for i in poc.get("invoices", []) if not i.get("division") or i.get("division") == division]


def _invoice_date(inv: dict) -> date | None:
    return parse_date(inv.get("invoiced_at") or inv.get("invoice_date") or inv.get("date"))


def _invoice_due(inv: dict) -> date | None:
    return parse_date(inv.get("due_date") or inv.get("maturity_date") or inv.get("scadenta") or inv.get("due_at"))


def _prognosis_for_month(prog: Any, month: int) -> Any:
    if not isinstance(prog, dict):
        return None
    for key in (f"{month:02d}", str(month), _MONTHS_EN[month - 1], MONTHS_RO[month - 1]):
        for k, v in prog.items():
            if str(k).lower() == key:
                return v
    for v in prog.values():
        if isinstance(v, dict):
            found = _prognosis_for_month(v, month)
            if found is not None:
                return found
    return None


def _prognosis_months(prog: Any) -> dict[int, Any]:
    return {m: v for m in range(1, 13) if (v := _prognosis_for_month(prog, m)) is not None}


# ── descrieri ───────────────────────────────────────────────────

@dataclass(frozen=True, kw_only=True)
class Desc:
    key: str
    name: str
    icon: str
    value: Callable[[MyEngieEntity], Any]
    attrs: Callable[[MyEngieEntity], dict] | None = None
    unit: str | None = None
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None
    category: EntityCategory | None = None
    only: str | None = None  # limitează la o utilitate


# — per utilitate —

def _balance(e: MyEngieEntity) -> float:
    return round(to_float(e.poc_data.get("balance", {}).get("total")), 2)


def _balance_attrs(e: MyEngieEntity) -> dict:
    bal = e.poc_data.get("balance", {})
    return {
        "Conturi contract": ", ".join(str(a) for a in e.poc_data.get("contract_accounts", [])),
        "Facturi în sold": len(bal.get("invoices") or []),
        "Plăți în procesare": len(bal.get("pending") or []),
    }


def _unpaid(e: MyEngieEntity) -> list[dict]:
    return [i for i in _invoices_for(e.poc_data, e._division) if to_float(i.get("unpaid")) > 0]


def _unpaid_total(e: MyEngieEntity) -> float:
    return round(sum(to_float(i.get("unpaid")) for i in _unpaid(e)), 2)


def _unpaid_attrs(e: MyEngieEntity) -> dict:
    attrs: dict[str, Any] = {}
    today = date.today()
    overdue = 0
    for inv in sorted(_unpaid(e), key=lambda i: _invoice_date(i) or date.min):
        due = _invoice_due(inv)
        label = f"Factura din {ro_date(inv.get('invoiced_at'))}"
        info = ron(inv.get("unpaid"))
        if due:
            days = (due - today).days
            if days < 0:
                overdue += 1
                info += f" – depășită cu {-days} zile"
            else:
                info += f" – scadentă în {days} zile ({ro_date(due)})"
        attrs[label] = info
    attrs["Facturi neachitate"] = len(_unpaid(e))
    attrs["Facturi depășite"] = overdue
    return attrs


def _last_invoice(e: MyEngieEntity) -> dict | None:
    invs = [i for i in _invoices_for(e.poc_data, e._division) if _invoice_date(i)]
    return max(invs, key=_invoice_date) if invs else None


def _last_invoice_attrs(e: MyEngieEntity) -> dict:
    inv = _last_invoice(e)
    if not inv:
        return {}
    return {
        "Număr factură": inv.get("invoice_number") or inv.get("number") or inv.get("document_number") or "—",
        "Data emiterii": ro_date(inv.get("invoiced_at")),
        "Data scadenței": ro_date(_invoice_due(inv)),
        "Rest de plată": ron(inv.get("unpaid")),
        "Achitată": "Da" if to_float(inv.get("unpaid")) == 0 else "Nu",
    }


def _year_invoices(e: MyEngieEntity) -> list[dict]:
    year = date.today().year
    return sorted(
        (i for i in _invoices_for(e.poc_data, e._division) if (d := _invoice_date(i)) and d.year == year),
        key=_invoice_date,
    )


def _year_invoices_attrs(e: MyEngieEntity) -> dict:
    invs = _year_invoices(e)
    attrs = {f"Emisă pe {ro_date(i.get('invoiced_at'))}": ron(i.get("total")) for i in invs}
    attrs["Total facturat"] = ron(sum(to_float(i.get("total")) for i in invs))
    return attrs


def _year_payments(e: MyEngieEntity) -> list[dict]:
    year = date.today().year
    return sorted(
        (p for p in e.poc_data.get("payments", []) if (d := parse_date(p.get("paid_at"))) and d.year == year),
        key=lambda p: parse_date(p.get("paid_at")),
    )


def _year_payments_attrs(e: MyEngieEntity) -> dict:
    pays = _year_payments(e)
    attrs = {}
    for p in pays:
        val = ron(p.get("amount"))
        if p.get("payment_method"):
            val += f" ({p['payment_method']})"
        attrs[f"Plată pe {ro_date(p.get('paid_at'))}"] = val
    attrs["Total plătit"] = ron(sum(to_float(p.get("amount")) for p in pays))
    return attrs


def _read_window(e: MyEngieEntity) -> tuple[date | None, date | None]:
    for inst in e.poc_data.get("installations", {}).values():
        if inst["division"] != e._division:
            continue
        nrd = (inst.get("reading") or {}).get("next_read_dates") or {}
        start, end = parse_date(nrd.get("startDate")), parse_date(nrd.get("endDate"))
        if start or end:
            return start, end
    return None, None


def _can_read(e: MyEngieEntity) -> str:
    start, end = _read_window(e)
    return "Da" if start and end and start <= date.today() <= end else "Nu"


def _can_read_attrs(e: MyEngieEntity) -> dict:
    start, end = _read_window(e)
    attrs = {"Început perioadă": ro_date(start), "Sfârșit perioadă": ro_date(end)}
    if start and date.today() < start:
        attrs["Zile până la deschidere"] = (start - date.today()).days
    if end and start and start <= date.today() <= end:
        attrs["Zile rămase"] = (end - date.today()).days
    return attrs


def _consumption_year(e: MyEngieEntity) -> list[tuple[int, float]]:
    year = str(date.today().year)
    key = "consum_gaz" if e._division == "gaz" else "consum_elec"
    out = []
    for item in e.poc_data.get("consumption", []):
        month = str(item.get("invoiced_at") or item.get("month") or "")
        if month.startswith(year) and item.get(key) is not None:
            try:
                out.append((int(month[5:7]), to_float(item.get(key))))
            except ValueError:
                continue
    return sorted(out)


def _consumption_attrs(e: MyEngieEntity) -> dict:
    return {f"Consum {MONTHS_RO[m - 1]}": f"{round(v, 2)} kWh" for m, v in _consumption_year(e)}


def _contract(e: MyEngieEntity) -> dict | None:
    for item in (e.coordinator.data or {}).get("contracts", []):
        if str(item.get("poc_number")) != e._poc:
            continue
        for c in item.get("contracts") or []:
            if isinstance(c, dict) and c.get("division") == e._division:
                return c
    return None


def _contract_attrs(e: MyEngieEntity) -> dict:
    c = _contract(e)
    if not c:
        return {}
    return {
        "Nr. contract": str(c.get("contract_number", "")),
        "Descriere": c.get("description", ""),
        "Început": ro_date(c.get("from_date")),
        "Sfârșit": ro_date(c.get("to_date")),
        "Cont contract": str(c.get("contract_account_number", "")),
        "Instalație": str(c.get("installation_number", "")),
        "Document disponibil": "Da" if c.get("hasDocument") else "Nu",
    }


def _partner(e: MyEngieEntity) -> dict | None:
    partners = (e.coordinator.data or {}).get("partners", [])
    pa = e.poc_data.get("pa")
    return next((p for p in partners if str(p.get("pa")) == str(pa)), partners[0] if partners else None)


def _partner_attrs(e: MyEngieEntity) -> dict:
    p = _partner(e)
    if not p:
        return {}
    addresses = p.get("addresses") or []
    return {
        "Tip": "Persoană fizică" if p.get("type") == "persoana_fizica" else p.get("type", ""),
        "Email": p.get("partnerEmail") or "—",
        "Telefon mobil": p.get("partnerMobile") or "—",
        "Telefon fix": p.get("partnerPhone") or "—",
        "Cod client (PA)": str(p.get("pa", "")),
        "Adresă corespondență": addresses[0].get("inline", "") if addresses and isinstance(addresses[0], dict) else "—",
        "Adresă loc consum": _address(e.poc_data.get("address")),
    }


def _address(addr: Any) -> str:
    if isinstance(addr, str):
        return addr
    if not isinstance(addr, dict):
        return "—"
    if addr.get("inline"):
        return addr["inline"]
    parts = [" ".join(x for x in (addr.get("street"), addr.get("number")) if x)]
    for k, p in (("block", "bl."), ("staircase", "sc."), ("apartment", "ap.")):
        if addr.get(k):
            parts.append(f"{p} {addr[k]}")
    parts += [addr.get("city"), addr.get("county")]
    return ", ".join(x for x in parts if x) or "—"


def _inspection(e: MyEngieEntity) -> dict:
    return next((v for v in e.poc_data.get("inspection", {}).values() if v), {})


def _inspection_state(e: MyEngieEntity) -> str:
    insp = _inspection(e)
    nxt = parse_date(insp.get("next_inspection_date"))
    if insp.get("next_inspection_is_overdue") or (nxt and nxt < date.today()):
        return "Expirată"
    return "Validă" if nxt else "Necunoscută"


def _inspection_attrs(e: MyEngieEntity) -> dict:
    insp = _inspection(e)
    if not insp:
        return {}
    nxt = parse_date(insp.get("next_inspection_date"))
    kind = {"V": "Verificare", "R": "Revizie"}.get(insp.get("next_inspection_type"), insp.get("next_inspection_type") or "—")
    attrs = {
        "Ultima revizie": ro_date(insp.get("last_revision_date")),
        "Ultima verificare": ro_date(insp.get("last_verify_date")),
        "Următoarea inspecție": ro_date(nxt),
        "Tip următoare inspecție": kind,
    }
    if nxt:
        attrs["Zile rămase"] = (nxt - date.today()).days
    tech = next((v for v in e.poc_data.get("technical", {}).values() if v), None)
    if isinstance(tech, dict):
        for k, v in tech.items():
            if isinstance(v, (str, int, float, bool)) and v not in ("", None):
                attrs[f"Servicii tehnice – {k}"] = v
    return attrs


def _green_bill(e: MyEngieEntity) -> str:
    gb = e.poc_data.get("green_bill") or {}
    val = gb.get("has_greenbill", gb.get("status"))
    if val is None:
        return "Necunoscut"
    return "Activă" if val in (True, "true", "active", "activ", 1) else "Inactivă"


def _distributor(e: MyEngieEntity) -> str | None:
    d = e.poc_data.get("distributor")
    if isinstance(d, list):
        d = next((x for x in d if isinstance(x, dict) and x.get("division", e._division) == e._division), d[0] if d else None)
    if isinstance(d, dict):
        return d.get("name") or d.get("distributor_name") or d.get("denumire") or d.get("distributor")
    return d if isinstance(d, str) else None


def _distributor_attrs(e: MyEngieEntity) -> dict:
    d = e.poc_data.get("distributor")
    if isinstance(d, list):
        d = d[0] if d and isinstance(d[0], dict) else {}
    if not isinstance(d, dict):
        return {}
    return {k: v for k, v in d.items() if isinstance(v, (str, int, float)) and v != ""}


UTILITY_SENSORS: tuple[Desc, ...] = (
    Desc(key="sold_total", name="Sold total", icon="mdi:cash", value=_balance, attrs=_balance_attrs,
         unit="RON", device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.TOTAL),
    Desc(key="factura_restanta", name="Factură restantă", icon="mdi:file-document-alert",
         value=lambda e: "Da" if _unpaid(e) else "Nu", attrs=_unpaid_attrs),
    Desc(key="rest_de_plata", name="Rest de plată", icon="mdi:cash-clock", value=_unpaid_total,
         unit="RON", device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.TOTAL),
    Desc(key="ultima_factura", name="Ultima factură", icon="mdi:receipt-text",
         value=lambda e: round(to_float((_last_invoice(e) or {}).get("total")), 2) if _last_invoice(e) else None,
         attrs=_last_invoice_attrs, unit="RON", device_class=SensorDeviceClass.MONETARY),
    Desc(key="arhiva_facturi", name="Facturi anul curent", icon="mdi:file-document-multiple-outline",
         value=lambda e: len(_year_invoices(e)), attrs=_year_invoices_attrs, state_class=SensorStateClass.MEASUREMENT),
    Desc(key="arhiva_plati", name="Plăți anul curent", icon="mdi:cash-check",
         value=lambda e: len(_year_payments(e)), attrs=_year_payments_attrs, state_class=SensorStateClass.MEASUREMENT),
    Desc(key="citire_permisa", name="Citire permisă", icon="mdi:calendar-clock", value=_can_read, attrs=_can_read_attrs),
    Desc(key="consum_anual", name="Consum facturat anul curent", icon="mdi:chart-bar",
         value=lambda e: round(sum(v for _, v in _consumption_year(e)), 2), attrs=_consumption_attrs,
         unit=UnitOfEnergy.KILO_WATT_HOUR, device_class=SensorDeviceClass.ENERGY, state_class=SensorStateClass.TOTAL),
    Desc(key="date_contract", name="Contract", icon="mdi:file-sign",
         value=lambda e: (str((_contract(e) or {}).get("status") or "Necunoscut")).capitalize(), attrs=_contract_attrs,
         category=EntityCategory.DIAGNOSTIC),
    Desc(key="date_utilizator", name="Titular", icon="mdi:account-details",
         value=lambda e: (f"{(_partner(e) or {}).get('firstName', '')} {(_partner(e) or {}).get('lastName', '')}".strip() or None),
         attrs=_partner_attrs, category=EntityCategory.DIAGNOSTIC),
    Desc(key="factura_electronica", name="Factură electronică", icon="mdi:leaf", value=_green_bill,
         category=EntityCategory.DIAGNOSTIC),
    Desc(key="distribuitor", name="Distribuitor", icon="mdi:transmission-tower", value=_distributor,
         attrs=_distributor_attrs, category=EntityCategory.DIAGNOSTIC),
    Desc(key="revizie_tehnica", name="Revizie tehnică", icon="mdi:wrench-clock", value=_inspection_state,
         attrs=_inspection_attrs, only="gaz"),
)


# — per contor —

_HISTORY_TYPES = {
    "autocitire": "autocitire", "autocitire aplicație mobilă": "autocitire", "autocitire aplicatie mobila": "autocitire",
    "citire reprezentant distribuitor": "distribuitor", "estimare convenție consum": "estimare",
    "estimare conventie consum": "estimare", "e": "estimare", "a": "autocitire", "d": "distribuitor",
}


def _index_value(e: MyEngieEntity) -> Any:
    val = (e.inst_data.get("reading") or {}).get("last_index")
    try:
        return float(str(val).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _index_attrs(e: MyEngieEntity) -> dict:
    inst = e.inst_data
    r = inst.get("reading") or {}
    nrd = r.get("next_read_dates") or {}
    attrs = {
        "Instalație": inst.get("installation"),
        "POD": inst.get("pod") or "—",
        "Serie contor": inst.get("serial") or "—",
        "Cod autocitire": r.get("autocit") or "—",
        "Permite index": "Da" if r.get("permite_index") else "Nu",
        "Perioadă autocitire": f"{ro_date(nrd.get('startDate'))} – {ro_date(nrd.get('endDate'))}" if nrd else "—",
    }
    for k in ("last_index_date", "last_read_date", "data_ultim_index"):
        if r.get(k):
            attrs["Data ultimului index"] = ro_date(r[k])
            break
    hist = inst.get("history") or []
    if hist:
        attrs["Ultima citire (istoric)"] = f"{hist[0].get('index')} – {ro_date(hist[0].get('data'))}"
    return attrs


def _history_attrs(e: MyEngieEntity) -> dict:
    attrs: dict[str, Any] = {}
    for item in (e.inst_data.get("history") or [])[:24]:
        tip = _HISTORY_TYPES.get(str(item.get("tip_citire") or "").strip().lower(), item.get("tip_citire") or "")
        label = f"{ro_date(item.get('data'))}" + (f" ({tip})" if tip else "")
        attrs[label] = item.get("index")
    return attrs


def _prognosis_value(e: MyEngieEntity) -> Any:
    v = _prognosis_for_month(e.inst_data.get("prognosis"), date.today().month)
    return to_float(v) if v is not None else None


def _prognosis_attrs(e: MyEngieEntity) -> dict:
    return {MONTHS_RO[m - 1].capitalize(): v for m, v in _prognosis_months(e.inst_data.get("prognosis")).items()}


METER_SENSORS: tuple[Desc, ...] = (
    Desc(key="index_contor", name="Index contor", icon="mdi:counter", value=_index_value, attrs=_index_attrs,
         state_class=SensorStateClass.TOTAL_INCREASING),
    Desc(key="istoric_citiri", name="Istoric citiri", icon="mdi:history",
         value=lambda e: len(e.inst_data.get("history") or []), attrs=_history_attrs),
    Desc(key="conventie_consum", name="Convenție consum luna curentă", icon="mdi:chart-timeline-variant",
         value=_prognosis_value, attrs=_prognosis_attrs),
)


class MyEngieSensor(MyEngieEntity, SensorEntity):
    def __init__(self, coordinator: MyEngieCoordinator, poc: str, division: str, desc: Desc,
                 installation: str | None = None) -> None:
        super().__init__(coordinator, poc, division, desc.key, installation)
        self._desc = desc
        self._attr_name = f"{desc.name} {installation}" if installation else desc.name
        self._attr_icon = desc.icon
        self._attr_native_unit_of_measurement = desc.unit
        self._attr_device_class = desc.device_class
        self._attr_state_class = desc.state_class
        self._attr_entity_category = desc.category
        if desc.key == "index_contor":
            if division == "gaz":
                self._attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
                self._attr_device_class = SensorDeviceClass.GAS
            else:
                self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
                self._attr_device_class = SensorDeviceClass.ENERGY

    @property
    def native_value(self) -> Any:
        return self._desc.value(self)

    @property
    def extra_state_attributes(self) -> dict | None:
        return self._desc.attrs(self) if self._desc.attrs else None


class MyEngieUnreadSensor(CoordinatorEntity[MyEngieCoordinator], SensorEntity):
    """Notificări necitite la nivel de cont."""

    _attr_has_entity_name = True
    _attr_name = "Notificări necitite"
    _attr_icon = "mdi:bell-badge"
    _attr_attribution = ATTRIBUTION
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: MyEngieCoordinator, entry: MyEngieConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{entry.unique_id or entry.entry_id}_notificari"
        self.entity_id = f"sensor.{DOMAIN}_notificari_necitite"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"account_{entry.entry_id}")},
            name=f"{NAME} cont",
            manufacturer="ENGIE România",
            model="Cont MyENGIE",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://my.engie.ro",
        )

    @property
    def native_value(self) -> int | None:
        return (self.coordinator.data or {}).get("unread")

    @property
    def extra_state_attributes(self) -> dict:
        attrs: dict[str, Any] = {}
        for n in (self.coordinator.data or {}).get("notifications", [])[:10]:
            title = n.get("title") or n.get("subject") or n.get("message") or "Notificare"
            when = ro_date(n.get("created_at") or n.get("date") or n.get("sent_at"))
            unread = n.get("read") is False or n.get("is_read") is False or n.get("seen") is False
            attrs[f"{when} – {str(title)[:60]}"] = "necitită" if unread else "citită"
        profile = (self.coordinator.data or {}).get("profile") or {}
        if profile.get("email"):
            attrs["Cont"] = profile["email"]
        return attrs


async def async_setup_entry(hass: HomeAssistant, entry: MyEngieConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [MyEngieUnreadSensor(coordinator, entry)]

    for poc_id, poc in (coordinator.data or {}).get("pocs", {}).items():
        for division in poc.get("divisions") or ["gaz"]:
            entities.extend(
                MyEngieSensor(coordinator, poc_id, division, d)
                for d in UTILITY_SENSORS
                if d.only in (None, division)
            )
        for inst_id, inst in poc.get("installations", {}).items():
            entities.extend(
                MyEngieSensor(coordinator, poc_id, inst["division"], d, inst_id) for d in METER_SENSORS
            )

    async_add_entities(entities)
