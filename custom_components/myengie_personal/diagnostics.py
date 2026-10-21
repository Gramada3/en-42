"""Diagnostic (date anonimizate) pentru MyENGIE (personal)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import MyEngieConfigEntry
from .const import CONF_TOKEN_DATA

REDACT = {
    CONF_PASSWORD, CONF_USERNAME, CONF_TOKEN_DATA, "access", "refresh", "id_token",
    "email", "partnerEmail", "partnerMobile", "partnerPhone", "phone", "mobile", "cnp_cui", "cnp",
    "firstName", "lastName", "first_name", "last_name", "inline", "street", "number", "addresses",
}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: MyEngieConfigEntry) -> dict[str, Any]:
    return {
        "entry": async_redact_data(dict(entry.data), REDACT),
        "options": dict(entry.options),
        "data": async_redact_data(entry.runtime_data.data or {}, REDACT),
    }
