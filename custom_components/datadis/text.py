"""Text platform for Datadis settings."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.text import TextEntity, TextEntityDescription, TextMode
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DatadisConfigEntry
from .api import DatadisApiClient, DatadisApiError, DatadisAuthError, DatadisCredentials
from .coordinator import DatadisCoordinator
from .const import (
    CONF_CUPS,
    CONF_DISTRIBUTOR_CODE,
    CONF_PASSWORD,
    CONF_POINT_TYPE,
    CONF_USERNAME,
    DEFAULT_DISTRIBUTOR_CODE,
    DEFAULT_POINT_TYPE,
)


@dataclass(frozen=True, kw_only=True)
class DatadisTextDescription(TextEntityDescription):
    """Datadis editable text setting description."""

    option_key: str
    fallback_data_key: str | None = None


TEXTS: tuple[DatadisTextDescription, ...] = (
    DatadisTextDescription(
        key="cups",
        name="CUPS",
        translation_key="cups",
        option_key=CONF_CUPS,
        fallback_data_key=CONF_CUPS,
        mode=TextMode.TEXT,
        icon="mdi:identifier",
    ),
    DatadisTextDescription(
        key="distributor_code",
        name="Distributor Code",
        translation_key="distributor_code",
        option_key=CONF_DISTRIBUTOR_CODE,
        fallback_data_key=CONF_DISTRIBUTOR_CODE,
        mode=TextMode.TEXT,
        icon="mdi:factory",
    ),
    DatadisTextDescription(
        key="point_type",
        name="Point Type",
        translation_key="point_type",
        option_key=CONF_POINT_TYPE,
        fallback_data_key=CONF_POINT_TYPE,
        mode=TextMode.TEXT,
        icon="mdi:tune",
    ),
)


async def async_setup_entry(
    hass,
    entry: DatadisConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Datadis text entities from config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        DatadisOptionText(coordinator, entry, description) for description in TEXTS
    )


class DatadisOptionText(CoordinatorEntity[DatadisCoordinator], TextEntity):
    """Editable text setting mapped to config entry options."""

    entity_description: DatadisTextDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DatadisCoordinator,
        entry: DatadisConfigEntry,
        description: DatadisTextDescription,
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
    def native_value(self) -> str:
        if self.entity_description.option_key in self._entry.options:
            value = self._entry.options[self.entity_description.option_key]
        elif self.entity_description.fallback_data_key:
            value = self._entry.data.get(self.entity_description.fallback_data_key, "")
        else:
            value = ""

        if value is None:
            value = ""
        if self.entity_description.option_key == CONF_DISTRIBUTOR_CODE and value == "":
            return DEFAULT_DISTRIBUTOR_CODE
        if self.entity_description.option_key == CONF_POINT_TYPE and value == "":
            return DEFAULT_POINT_TYPE
        return str(value)

    async def async_set_value(self, value: str) -> None:
        new_value = value.strip()
        if self.entity_description.option_key == CONF_POINT_TYPE and new_value not in {
            "1",
            "5",
        }:
            raise HomeAssistantError("Point Type must be 1 or 5")

        if new_value == self.native_value:
            return

        options = dict(self._entry.options)
        options[self.entity_description.option_key] = new_value

        if self.entity_description.option_key in {
            CONF_CUPS,
            CONF_DISTRIBUTOR_CODE,
            CONF_POINT_TYPE,
        }:
            await self._async_validate_runtime_settings(options)

        self.hass.config_entries.async_update_entry(self._entry, options=options)

    async def _async_validate_runtime_settings(self, candidate_options: dict) -> None:
        cups = candidate_options.get(CONF_CUPS, self._entry.data[CONF_CUPS]).strip()
        distributor_code = candidate_options.get(
            CONF_DISTRIBUTOR_CODE,
            self._entry.data.get(CONF_DISTRIBUTOR_CODE, DEFAULT_DISTRIBUTOR_CODE),
        ).strip()
        point_type = candidate_options.get(
            CONF_POINT_TYPE,
            self._entry.data.get(CONF_POINT_TYPE, DEFAULT_POINT_TYPE),
        ).strip()

        credentials = DatadisCredentials(
            username=self._entry.data[CONF_USERNAME],
            password=self._entry.data[CONF_PASSWORD],
        )
        client = DatadisApiClient(
            hass=self.hass,
            credentials=credentials,
            cups=cups,
            distributor_code=distributor_code,
            point_type=point_type,
        )
        try:
            await client.async_validate_access()
        except (DatadisAuthError, DatadisApiError) as err:
            raise HomeAssistantError(
                f"Datadis validation failed for updated settings: {err}"
            ) from err
