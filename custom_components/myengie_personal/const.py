"""Constante pentru integrarea MyENGIE (personal)."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "myengie_personal"
NAME = "MyENGIE"
ATTRIBUTION = "Date furnizate de ENGIE România"

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.NUMBER, Platform.BUTTON]

# ── Configurare ────────────────────────────────────────────────
CONF_UPDATE_INTERVAL = "update_interval"
CONF_TOKEN_DATA = "token_data"
DEFAULT_UPDATE_INTERVAL = 3600  # secunde
MIN_UPDATE_INTERVAL = 900
HEAVY_EVERY = 6  # plăți + factură electronică + notificări la fiecare al 6-lea ciclu

# ── API ────────────────────────────────────────────────────────
API_BASE = "https://gwss.engie.ro/myservices"
URL_LOGIN = f"{API_BASE}/v1/login"
URL_OAUTH_TOKEN = "https://auth.engie.ro/oauth/token"
OAUTH_CLIENT_ID = "hMpDTLmC0C8szydob7zqUs231mQoDuyK"
OAUTH_AUDIENCE = "https://myservices.engie.ro"

API_TIMEOUT = 30
TOKEN_DEFAULT_TTL = 7200
TOKEN_MARGIN = 300

APP_VERSION = "2.1.11"
APP_BUILD = "177"
DEVICE_HEADERS: dict[str, str] = {
    "Accept": "application/json",
    "source": "android",
    "App-Version": APP_VERSION,
    "App-Build": APP_BUILD,
    "OS-Version": "14",
    "OS-Platform": "android",
    "Device-Type": "phone",
    "Device-Manufacturer": "Google",
    "Device-Model": "Pixel 8",
    "Screen-Height": "2400",
    "Screen-Width": "1080",
    "Device-Id": "ha-myengie-personal",
    "User-Agent": f"MyEngie/{APP_VERSION} (Android; ro.engie.agentia; build {APP_BUILD})",
}

# ── Utilități ──────────────────────────────────────────────────
DIVISIONS: dict[str, dict[str, str]] = {
    "gaz": {"slug": "gaz", "label": "Gaz", "icon": "mdi:fire"},
    "elec": {"slug": "electricitate", "label": "Energie electrică", "icon": "mdi:flash"},
}

MONTHS_RO = [
    "ianuarie", "februarie", "martie", "aprilie", "mai", "iunie",
    "iulie", "august", "septembrie", "octombrie", "noiembrie", "decembrie",
]

SERVICE_SUBMIT_INDEX = "trimite_index"
SERVICE_REFRESH = "actualizeaza"
