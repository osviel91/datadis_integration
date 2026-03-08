"""Button platform for Datadis."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DatadisConfigEntry
from .coordinator import DatadisCoordinator
from .const import CONF_CUPS


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DatadisConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Datadis buttons from a config entry."""
    async_add_entities([DatadisRefreshButton(entry.runtime_data, entry)])


class DatadisRefreshButton(CoordinatorEntity[DatadisCoordinator], ButtonEntity):
    """Force-refresh button for Datadis."""

    _attr_has_entity_name = True
    _attr_name = "Refresh now"
    _attr_translation_key = "refresh_now"
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator: DatadisCoordinator, entry: DatadisConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_refresh_now"
        self._attr_device_info = {
            "identifiers": {("datadis", entry.entry_id)},
            "name": f"Datadis {entry.options.get(CONF_CUPS, entry.data['cups'])}",
            "manufacturer": "Datadis",
            "model": "Private API",
        }

    async def async_press(self) -> None:
        """Handle the button press."""
        await self.coordinator.async_request_refresh()
