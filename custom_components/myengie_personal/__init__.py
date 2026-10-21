"""Integrare MyENGIE (personal) pentru Home Assistant."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import MyEngieApi, MyEngieError
from .const import (
    CONF_TOKEN_DATA,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    PLATFORMS,
    SERVICE_REFRESH,
    SERVICE_SUBMIT_INDEX,
)
from .coordinator import MyEngieCoordinator

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type MyEngieConfigEntry = ConfigEntry[MyEngieCoordinator]

SUBMIT_SCHEMA = vol.Schema(
    {
        vol.Required("installation"): cv.string,
        vol.Required("index"): vol.All(vol.Coerce(int), vol.Range(min=0)),
    }
)


def _coordinators(hass: HomeAssistant) -> list[MyEngieCoordinator]:
    return [
        e.runtime_data
        for e in hass.config_entries.async_loaded_entries(DOMAIN)
        if isinstance(getattr(e, "runtime_data", None), MyEngieCoordinator)
    ]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Înregistrează serviciile globale."""

    async def _submit(call: ServiceCall) -> dict:
        installation = str(call.data["installation"])
        for coordinator in _coordinators(hass):
            if coordinator.find_installation(installation):
                try:
                    result = await coordinator.async_submit_index(installation, call.data["index"])
                except MyEngieError as err:
                    raise HomeAssistantError(str(err)) from err
                return {"raspuns": result if isinstance(result, (dict, list)) else str(result)}
        raise HomeAssistantError(f"Instalația {installation} nu a fost găsită în niciun cont MyENGIE")

    async def _refresh(call: ServiceCall) -> None:
        for coordinator in _coordinators(hass):
            await coordinator.async_request_refresh()

    hass.services.async_register(
        DOMAIN, SERVICE_SUBMIT_INDEX, _submit, schema=SUBMIT_SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
    hass.services.async_register(DOMAIN, SERVICE_REFRESH, _refresh)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: MyEngieConfigEntry) -> bool:
    api = MyEngieApi(async_get_clientsession(hass), entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
    api.import_tokens(entry.data.get(CONF_TOKEN_DATA))

    interval = entry.options.get(CONF_UPDATE_INTERVAL, entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL))
    coordinator = MyEngieCoordinator(hass, entry, api, int(interval))
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload_on_options))
    return True


async def _reload_on_options(hass: HomeAssistant, entry: MyEngieConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: MyEngieConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
