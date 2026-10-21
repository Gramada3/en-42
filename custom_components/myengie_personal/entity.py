"""Clasă de bază pentru entitățile MyENGIE (personal)."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DIVISIONS, DOMAIN, NAME
from .coordinator import MyEngieCoordinator


class MyEngieEntity(CoordinatorEntity[MyEngieCoordinator]):
    """Entitate legată de un loc de consum + utilitate (un device per utilitate)."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(
        self,
        coordinator: MyEngieCoordinator,
        poc: str,
        division: str,
        key: str,
        installation: str | None = None,
        platform: str = "sensor",
    ) -> None:
        super().__init__(coordinator)
        self._poc = poc
        self._division = division
        self._installation = installation
        slug = DIVISIONS[division]["slug"]
        suffix = f"_{installation}" if installation else ""
        self._attr_unique_id = f"{DOMAIN}_{poc}_{slug}_{key}{suffix}"
        self.entity_id = f"{platform}.{DOMAIN}_{poc}_{slug}_{key}{suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{poc}_{slug}")},
            name=f"{NAME} {poc} {DIVISIONS[division]['label']}",
            manufacturer="ENGIE România",
            model=f"Loc de consum – {DIVISIONS[division]['label']}",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://my.engie.ro",
        )

    @property
    def poc_data(self) -> dict:
        return (self.coordinator.data or {}).get("pocs", {}).get(self._poc, {})

    @property
    def inst_data(self) -> dict:
        if not self._installation:
            return {}
        return self.poc_data.get("installations", {}).get(self._installation, {})

    @property
    def available(self) -> bool:
        return super().available and bool(self.poc_data)
