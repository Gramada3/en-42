"""Câmp numeric pentru indexul de trimis (înlocuiește input_number-ul creat manual)."""

from __future__ import annotations

from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MyEngieConfigEntry
from .entity import MyEngieEntity


class MyEngieIndexNumber(MyEngieEntity, RestoreNumber):
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = 99_999_999
    _attr_native_step = 1
    _attr_icon = "mdi:numeric"

    def __init__(self, coordinator, poc: str, division: str, installation: str) -> None:
        super().__init__(coordinator, poc, division, "index_nou", installation, platform="number")
        self._attr_name = f"Index de trimis {installation}"
        self._attr_native_unit_of_measurement = "m³" if division == "gaz" else "kWh"
        self._value: float | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last and last.native_value is not None:
            self._value = last.native_value

    @property
    def native_value(self) -> float | None:
        if self._value is not None:
            return self._value
        val = (self.inst_data.get("reading") or {}).get("last_index")
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        self._value = value
        self.async_write_ha_state()


async def async_setup_entry(hass: HomeAssistant, entry: MyEngieConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        MyEngieIndexNumber(coordinator, poc_id, inst["division"], inst_id)
        for poc_id, poc in (coordinator.data or {}).get("pocs", {}).items()
        for inst_id, inst in poc.get("installations", {}).items()
    )
