"""Sensor platform for Datadis."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DatadisConfigEntry
from .coordinator import DatadisCoordinator, DatadisData
from .const import CONF_CUPS


@dataclass(frozen=True, kw_only=True)
class DatadisSensorEntityDescription(SensorEntityDescription):
    """Datadis sensor entity description."""

    value_fn: Callable[[DatadisData], float | datetime | None]


SENSORS: tuple[DatadisSensorEntityDescription, ...] = (
    DatadisSensorEntityDescription(
        key="monthly_consumption",
        name="Monthly Consumption",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:calendar-month",
        value_fn=lambda data: data.monthly_consumption_kwh,
    ),
    DatadisSensorEntityDescription(
        key="daily_consumption",
        name="Daily Consumption",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:calendar",
        value_fn=lambda data: data.daily_consumption_kwh,
    ),
    DatadisSensorEntityDescription(
        key="latest_hour_consumption",
        name="Latest Hour Consumption",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash",
        value_fn=lambda data: data.latest_hour_consumption_kwh,
    ),
    DatadisSensorEntityDescription(
        key="monthly_peak_power",
        name="Monthly Peak Power",
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:chart-line",
        value_fn=lambda data: data.monthly_peak_power_kw,
    ),
    DatadisSensorEntityDescription(
        key="last_successful_update",
        name="Last Successful Update",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-check-outline",
        value_fn=lambda data: data.last_successful_update,
    ),
    DatadisSensorEntityDescription(
        key="next_allowed_query_at",
        name="Next Allowed Query",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-outline",
        value_fn=lambda data: data.next_allowed_query_at,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DatadisConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Datadis sensors from a config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        DatadisSensor(coordinator, entry, description) for description in SENSORS
    )


class DatadisSensor(CoordinatorEntity[DatadisCoordinator], SensorEntity):
    """Datadis sensor entity."""

    entity_description: DatadisSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DatadisCoordinator,
        entry: ConfigEntry,
        description: DatadisSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_translation_key = description.key
        self._attr_device_info = {
            "identifiers": {("datadis", entry.entry_id)},
            "name": f"Datadis {entry.options.get(CONF_CUPS, entry.data['cups'])}",
            "manufacturer": "Datadis",
            "model": "Private API",
        }

    @property
    def native_value(self) -> float | datetime | None:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        if self.coordinator.data is None:
            return None

        if self.entity_description.key == "latest_hour_consumption":
            measured_at = self.coordinator.data.latest_measurement_at
            if measured_at is None:
                return None
            return {"measurement_at": measured_at.isoformat()}

        if self.entity_description.key == "monthly_consumption":
            attrs: dict[str, str] = {
                "is_fallback_period": str(
                    self.coordinator.data.monthly_consumption_is_fallback
                ).lower()
            }
            if self.coordinator.data.data_period_start is not None:
                attrs["data_period_start"] = (
                    self.coordinator.data.data_period_start.isoformat()
                )
            if self.coordinator.data.data_period_end is not None:
                attrs["data_period_end"] = self.coordinator.data.data_period_end.isoformat()
            return attrs

        if self.entity_description.key == "daily_consumption":
            if self.coordinator.data.daily_consumption_date is None:
                return None
            return {
                "consumption_date": self.coordinator.data.daily_consumption_date.date().isoformat()
            }

        return None
