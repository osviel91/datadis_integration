"""Binary sensor platform for Datadis."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DatadisConfigEntry
from .coordinator import DatadisCoordinator
from .const import CONF_CUPS


async def async_setup_entry(hass, entry: DatadisConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up Datadis binary sensors from a config entry."""
    async_add_entities([DatadisRateLimitBinarySensor(entry.runtime_data, entry)])


class DatadisRateLimitBinarySensor(CoordinatorEntity[DatadisCoordinator], BinarySensorEntity):
    """Indicates if Datadis rate limit is currently active."""

    _attr_has_entity_name = True
    _attr_name = "Rate Limit Reached"
    _attr_translation_key = "rate_limit_reached"
    _attr_icon = "mdi:clock-alert-outline"

    def __init__(self, coordinator: DatadisCoordinator, entry: DatadisConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_rate_limit_reached"
        self._attr_device_info = {
            "identifiers": {("datadis", entry.entry_id)},
            "name": f"Datadis {entry.options.get(CONF_CUPS, entry.data['cups'])}",
            "manufacturer": "Datadis",
            "model": "Private API",
        }

    @property
    def is_on(self) -> bool:
        if self.coordinator.data is None:
            return False
        return self.coordinator.data.rate_limit_reached
