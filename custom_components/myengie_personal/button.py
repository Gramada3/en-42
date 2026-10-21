"""Butoane: trimitere autocitire per contor + actualizare manuală."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MyEngieConfigEntry
from .api import MyEngieError
from .const import DIVISIONS, DOMAIN
from .entity import MyEngieEntity

_LOGGER = logging.getLogger(__name__)


class MyEngieSubmitButton(MyEngieEntity, ButtonEntity):
    def __init__(self, coordinator, poc: str, division: str, installation: str) -> None:
        super().__init__(coordinator, poc, division, "trimite_index", installation, platform="button")
        self._attr_name = f"Trimite index {installation}"
        self._attr_icon = DIVISIONS[division]["icon"]
        slug = DIVISIONS[division]["slug"]
        self._number_uid = f"{DOMAIN}_{poc}_{slug}_index_nou_{installation}"

    async def async_press(self) -> None:
        registry = er.async_get(self.hass)
        number_id = registry.async_get_entity_id("number", DOMAIN, self._number_uid)
        state = self.hass.states.get(number_id) if number_id else None
        if not state or state.state in ("unknown", "unavailable"):
            raise HomeAssistantError("Completează mai întâi câmpul „Index de trimis”.")
        value = int(float(state.state))

        reading = self.inst_data.get("reading") or {}
        try:
            last = int(float(reading.get("last_index")))
        except (TypeError, ValueError):
            last = None
        if last is not None and value < last:
            raise HomeAssistantError(f"Indexul {value} este mai mic decât ultimul index ({last}).")

        try:
            await self.coordinator.async_submit_index(self._installation, value)
        except MyEngieError as err:
            raise HomeAssistantError(str(err)) from err
        _LOGGER.info("Autocitire %s trimisă pentru instalația %s", value, self._installation)


class MyEngieRefreshButton(MyEngieEntity, ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator, poc: str, division: str) -> None:
        super().__init__(coordinator, poc, division, "actualizeaza", platform="button")
        self._attr_name = "Actualizează datele"

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()


async def async_setup_entry(hass: HomeAssistant, entry: MyEngieConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    entities: list[ButtonEntity] = []
    for poc_id, poc in (coordinator.data or {}).get("pocs", {}).items():
        for division in poc.get("divisions") or []:
            entities.append(MyEngieRefreshButton(coordinator, poc_id, division))
        for inst_id, inst in poc.get("installations", {}).items():
            entities.append(MyEngieSubmitButton(coordinator, poc_id, inst["division"], inst_id))
    async_add_entities(entities)
