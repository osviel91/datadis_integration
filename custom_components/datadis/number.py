"""Number platform for Datadis settings."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberEntityDescription
from homeassistant.const import UnitOfTime
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DatadisConfigEntry
from .coordinator import DatadisCoordinator
from .const import (
    CONF_CUPS,
    CONF_QUERY_DAYS,
    CONF_RATE_LIMIT_COOLDOWN_HOURS,
    CONF_UPDATE_INTERVAL,
    DEFAULT_QUERY_DAYS,
    DEFAULT_RATE_LIMIT_COOLDOWN_HOURS,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    MAX_QUERY_DAYS,
    MAX_RATE_LIMIT_COOLDOWN_HOURS,
    MAX_UPDATE_INTERVAL_MINUTES,
    MIN_QUERY_DAYS,
    MIN_RATE_LIMIT_COOLDOWN_HOURS,
    MIN_UPDATE_INTERVAL_MINUTES,
)


@dataclass(frozen=True, kw_only=True)
class DatadisNumberDescription(NumberEntityDescription):
    """Datadis editable number setting description."""

    option_key: str
    default_value: int


NUMBERS: tuple[DatadisNumberDescription, ...] = (
    DatadisNumberDescription(
        key="update_interval_minutes",
        name="Update Interval",
        translation_key="update_interval_minutes",
        option_key=CONF_UPDATE_INTERVAL,
        default_value=DEFAULT_UPDATE_INTERVAL_MINUTES,
        native_min_value=MIN_UPDATE_INTERVAL_MINUTES,
        native_max_value=MAX_UPDATE_INTERVAL_MINUTES,
        native_step=5,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        mode="box",
        icon="mdi:timer-cog",
    ),
    DatadisNumberDescription(
        key="query_days",
        name="Query Window",
        translation_key="query_days",
        option_key=CONF_QUERY_DAYS,
        default_value=DEFAULT_QUERY_DAYS,
        native_min_value=MIN_QUERY_DAYS,
        native_max_value=MAX_QUERY_DAYS,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.DAYS,
        mode="box",
        icon="mdi:calendar-range",
    ),
    DatadisNumberDescription(
        key="rate_limit_cooldown_hours",
        name="Rate Limit Cooldown",
        translation_key="rate_limit_cooldown_hours",
        option_key=CONF_RATE_LIMIT_COOLDOWN_HOURS,
        default_value=DEFAULT_RATE_LIMIT_COOLDOWN_HOURS,
        native_min_value=MIN_RATE_LIMIT_COOLDOWN_HOURS,
        native_max_value=MAX_RATE_LIMIT_COOLDOWN_HOURS,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.HOURS,
        mode="box",
        icon="mdi:timer-sand",
    ),
)


async def async_setup_entry(
    hass,
    entry: DatadisConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Datadis number entities from config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        DatadisOptionNumber(coordinator, entry, description)
        for description in NUMBERS
    )


class DatadisOptionNumber(CoordinatorEntity[DatadisCoordinator], NumberEntity):
    """Editable number that maps to config entry options."""

    entity_description: DatadisNumberDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DatadisCoordinator,
        entry: DatadisConfigEntry,
        description: DatadisNumberDescription,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = {
            "identifiers": {("datadis", entry.entry_id)},
            "name": f"Datadis {entry.options.get(CONF_CUPS, entry.data['cups'])}",
            "manufacturer": "Datadis",
            "model": "Private API",
        }

    @property
    def native_value(self) -> float:
        value = self._entry.options.get(
            self.entity_description.option_key,
            self._entry.data.get(
                self.entity_description.option_key,
                self.entity_description.default_value,
            ),
        )
        return float(value)

    async def async_set_native_value(self, value: float) -> None:
        new_value = int(value)
        if int(self.native_value) == new_value:
            return

        options = dict(self._entry.options)
        options[self.entity_description.option_key] = new_value
        self.hass.config_entries.async_update_entry(self._entry, options=options)
