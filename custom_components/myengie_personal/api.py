"""Client asincron pentru API-ul MyENGIE România (gwss.engie.ro)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import date
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

from .const import (
    API_BASE,
    API_TIMEOUT,
    DEVICE_HEADERS,
    OAUTH_AUDIENCE,
    OAUTH_CLIENT_ID,
    TOKEN_DEFAULT_TTL,
    TOKEN_MARGIN,
    URL_LOGIN,
    URL_OAUTH_TOKEN,
)

_LOGGER = logging.getLogger(__name__)


class MyEngieError(Exception):
    """Eroare generică API."""


class MyEngieAuthError(MyEngieError):
    """Credențiale respinse."""


class MyEngieMaintenance(MyEngieError):
    """Serverul ENGIE răspunde cu pagină de mentenanță."""


_MAINTENANCE_HINTS = ("mentenan", "indisponibil", "actualizare engie", "maintenance")


def _unwrap(raw: Any) -> Any:
    """Răspunsurile ENGIE sunt de forma {"data": ...}; extrage conținutul."""
    if isinstance(raw, dict) and "data" in raw:
        return raw["data"]
    return raw


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


class MyEngieApi:
    """Gestionează autentificarea (login + refresh OAuth2) și apelurile REST."""

    def __init__(self, session: ClientSession, username: str, password: str) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._timeout = ClientTimeout(total=API_TIMEOUT)
        self._lock = asyncio.Lock()

        self._access: str | None = None
        self._refresh: str | None = None
        self._id_token: str | None = None
        self._expires_at: float = 0.0  # epoch

    # ── Token ────────────────────────────────────────────────────

    @property
    def token_valid(self) -> bool:
        return bool(self._access) and time.time() < self._expires_at - TOKEN_MARGIN

    def export_tokens(self) -> dict[str, Any] | None:
        if not self._access:
            return None
        return {
            "access": self._access,
            "refresh": self._refresh,
            "id_token": self._id_token,
            "expires_at": self._expires_at,
        }

    def import_tokens(self, data: dict[str, Any] | None) -> None:
        if not data:
            return
        self._access = data.get("access")
        self._refresh = data.get("refresh")
        self._id_token = data.get("id_token")
        self._expires_at = float(data.get("expires_at") or 0)

    def _set_tokens(self, access: str, refresh: str | None, id_token: str | None, ttl: Any) -> None:
        self._access = access
        if refresh:
            self._refresh = refresh
        if id_token:
            self._id_token = id_token
        try:
            seconds = int(ttl)
        except (TypeError, ValueError):
            seconds = TOKEN_DEFAULT_TTL
        # ENGIE trimite durata în secunde; protecție dacă ar trimite epoch
        if seconds > 10**9:
            self._expires_at = float(seconds)
        else:
            self._expires_at = time.time() + seconds

    # ── HTTP low-level ──────────────────────────────────────────

    @staticmethod
    async def _read_json(resp) -> Any:
        """ENGIE trimite uneori JSON cu Content-Type text/html."""
        text = await resp.text()
        if not text or not text.strip():
            return None
        try:
            return json.loads(text)
        except ValueError as err:
            low = text.lower()
            # Pagina „Actualizare engie.ro” are textul relevant mult după primele caractere,
            # așa că verificăm tot răspunsul. Orice pagină HTML pe un endpoint JSON înseamnă tot mentenanță.
            if any(k in low for k in _MAINTENANCE_HINTS) or low.lstrip().startswith(("<!doctype html", "<html")):
                raise MyEngieMaintenance("Serverul ENGIE este în mentenanță") from err
            raise MyEngieError(f"Răspuns non-JSON (HTTP {resp.status})") from err

    async def _login(self) -> None:
        headers = {**DEVICE_HEADERS, "Content-Type": "application/x-www-form-urlencoded"}
        try:
            async with self._session.post(
                URL_LOGIN,
                data={"username": self._username, "password": self._password},
                headers=headers,
                timeout=self._timeout,
            ) as resp:
                if resp.status in (400, 401, 403):
                    raise MyEngieAuthError(f"Login respins (HTTP {resp.status})")
                if resp.status != 200:
                    raise MyEngieError(f"Login eșuat (HTTP {resp.status})")
                body = await self._read_json(resp)
        except (ClientError, asyncio.TimeoutError) as err:
            raise MyEngieError(f"Eroare rețea la login: {err}") from err

        if not isinstance(body, dict) or body.get("error") is True:
            raise MyEngieAuthError("Utilizator sau parolă greșită")
        payload = body.get("data") if isinstance(body.get("data"), dict) else body
        token = payload.get("token") or payload.get("access_token")
        if not token:
            raise MyEngieError("Login fără token în răspuns")
        self._set_tokens(token, payload.get("refresh_token"), payload.get("id_token"), payload.get("exp"))
        _LOGGER.debug("Login MyENGIE reușit")

    async def _refresh_tokens(self) -> bool:
        if not self._refresh:
            return False
        headers = {**DEVICE_HEADERS, "Content-Type": "application/x-www-form-urlencoded"}
        form = {
            "client_id": OAUTH_CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": self._refresh,
            "audience": OAUTH_AUDIENCE,
        }
        try:
            async with self._session.post(
                URL_OAUTH_TOKEN, data=form, headers=headers, timeout=self._timeout
            ) as resp:
                if resp.status != 200:
                    if resp.status in (400, 401, 403):
                        self._refresh = None
                    return False
                body = await self._read_json(resp)
        except (ClientError, asyncio.TimeoutError, MyEngieError):
            return False
        if not isinstance(body, dict) or not body.get("access_token"):
            return False
        self._set_tokens(body["access_token"], body.get("refresh_token"), body.get("id_token"), body.get("expires_in"))
        _LOGGER.debug("Token MyENGIE reîmprospătat")
        return True

    async def ensure_auth(self, force: bool = False) -> None:
        if not force and self.token_valid:
            return
        async with self._lock:
            if not force and self.token_valid:
                return
            if await self._refresh_tokens():
                return
            await self._login()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: Any = None,
        form: dict | None = None,
        raw: bool = False,
    ) -> Any:
        url = path if path.startswith("http") else f"{API_BASE}/{path.lstrip('/')}"
        if params:
            params = {k: v for k, v in params.items() if v not in (None, "")}
        await self.ensure_auth()
        for attempt in (1, 2):
            headers = {**DEVICE_HEADERS, "Authorization": f"Bearer {self._access}"}
            kwargs: dict[str, Any] = {"params": params, "timeout": self._timeout}
            if form is not None:
                headers["Content-Type"] = "application/x-www-form-urlencoded"
                kwargs["data"] = form
            else:
                headers["Content-Type"] = "application/json"
                if json_body is not None:
                    kwargs["json"] = json_body
            try:
                async with self._session.request(method, url, headers=headers, **kwargs) as resp:
                    if resp.status == 401 and attempt == 1:
                        await self.ensure_auth(force=True)
                        continue
                    if resp.status == 404:
                        return None
                    if resp.status >= 400:
                        text = (await resp.text())[:300]
                        raise MyEngieError(f"{method} {path} → HTTP {resp.status}: {text}")
                    body = await self._read_json(resp)
                    return body if raw else _unwrap(body)
            except (ClientError, asyncio.TimeoutError) as err:
                raise MyEngieError(f"{method} {path}: {err}") from err
        raise MyEngieError(f"{method} {path}: autentificare eșuată")

    # ── Endpoint-uri ────────────────────────────────────────────

    async def user_profile(self) -> dict:
        return await self._request("GET", "v1/user/me") or {}

    async def places_of_consumption(self) -> list[dict]:
        data = await self._request("GET", "v1/placesofconsumption")
        if isinstance(data, dict):
            data = data.get("places_of_consumption")
        return [p for p in _as_list(data) if isinstance(p, dict)]

    async def contracts(self) -> list[dict]:
        return [c for c in _as_list(await self._request("GET", "v1/contracts")) if isinstance(c, dict)]

    async def partner_details(self) -> list[dict]:
        return [p for p in _as_list(await self._request("GET", "v1/partner/details")) if isinstance(p, dict)]

    async def divisions(self, poc: str, pa: str) -> dict:
        return await self._request("GET", f"v1/placesofconsumption/divisions/{poc}", params={"pa": pa}) or {}

    async def index(self, poc: str, division: str, pa: str, installation: str) -> Any:
        return await self._request(
            "GET",
            f"v1/index/{poc}",
            params={"division": division, "pa": pa, "installation_number": installation},
        )

    async def index_history(self, poc: str, division: str, autocit: str, start: date) -> Any:
        return await self._request(
            "POST",
            "v1/index/history",
            json_body={
                "autocit": autocit,
                "poc_number": poc,
                "division": division,
                "start_date": start.isoformat(),
            },
        )

    async def prognosis(self, poc: str, installation: str, pa: str) -> Any:
        return await self._request(
            "GET", f"v1/index/prognosis/{poc}", params={"installation_number": installation, "pa": pa}
        )

    async def consumption(self, poc: str, pa: str, start: date, end: date) -> Any:
        return await self._request(
            "GET",
            f"v1/index/consumption/{poc}",
            params={"startDate": start.isoformat(), "endDate": end.isoformat(), "pa": pa},
        )

    async def balance(self, contract_accounts: list[int]) -> dict:
        data = await self._request(
            "POST", "v1/invoices/ballance-details", json_body={"contract_account": contract_accounts}
        )
        return data if isinstance(data, dict) else {}

    async def invoices(self, poc: str, pa: str) -> Any:
        return await self._request("GET", f"v1/invoices/history/{poc}", params={"pa": pa})

    async def payments(self, pa: str, start: date, end: date) -> Any:
        return await self._request(
            "GET",
            f"v1/invoices/payment/history/{pa}",
            params={"startDate": start.isoformat(), "endDate": end.isoformat()},
        )

    async def inspection(self, poc: str, pod: str, pa: str) -> dict:
        data = await self._request("GET", f"v1/widgets/newrv/{poc}/{pod}", params={"pa": pa})
        return data if isinstance(data, dict) else {}

    async def technical_services(self, poc: str, pod: str, pa: str) -> Any:
        return await self._request(
            "GET", f"v1/notifications/technical-services-data/{poc}/{pod}", params={"pa": pa}
        )

    async def green_bill(self, poc: str, pa: str) -> dict:
        data = await self._request("GET", f"v1/placesofconsumption/{poc}/greenbill/status", params={"pa": pa})
        return data if isinstance(data, dict) else {}

    async def distributor(self, poc: str) -> Any:
        return await self._request("GET", f"v1/distributors/placesofconsumption/{poc}")

    async def notifications(self) -> Any:
        return await self._request("GET", "v1/notifications")

    async def notifications_unread(self) -> Any:
        return await self._request("GET", "v1/notifications/unread-number")

    async def submit_index(
        self, poc: str, pa: str, division: str, installation: str, value: int, meter_serial: str = ""
    ) -> Any:
        form = {
            "poc_number": poc,
            "pa": pa,
            "installation_number": installation,
            "division": division,
            "index": str(value),
        }
        if meter_serial:
            form["serie_contor"] = meter_serial
        return await self._request("POST", "v1/index", form=form, raw=True)
