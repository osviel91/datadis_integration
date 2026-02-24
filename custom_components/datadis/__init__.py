"""Datadis integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .api import DatadisApiClient, DatadisApiError, DatadisCredentials
from .const import (
    CONF_DISTRIBUTOR_CODE,
    CONF_QUERY_DAYS,
    CONF_UPDATE_INTERVAL,
    DEFAULT_DISTRIBUTOR_CODE,
    DEFAULT_QUERY_DAYS,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import DatadisCoordinator


DatadisConfigEntry = ConfigEntry[DatadisCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: DatadisConfigEntry) -> bool:
    """Set up Datadis from a config entry."""
    credentials = DatadisCredentials(
        username=entry.data["username"],
        password=entry.data["password"],
    )

    client = DatadisApiClient(
        hass=hass,
        credentials=credentials,
        cups=entry.data["cups"],
        distributor_code=entry.options.get(
            CONF_DISTRIBUTOR_CODE,
            entry.data.get(CONF_DISTRIBUTOR_CODE, DEFAULT_DISTRIBUTOR_CODE),
        ),
    )

    update_interval = entry.options.get(
        CONF_UPDATE_INTERVAL,
        entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_MINUTES),
    )
    query_days = entry.options.get(
        CONF_QUERY_DAYS,
        entry.data.get(CONF_QUERY_DAYS, DEFAULT_QUERY_DAYS),
    )

    coordinator = DatadisCoordinator(
        hass=hass,
        client=client,
        name=f"datadis_{entry.data['cups']}",
        update_interval_minutes=update_interval,
        query_days=query_days,
    )

    try:
        await coordinator.async_config_entry_first_refresh()
    except DatadisApiError as err:
        raise ConfigEntryNotReady(f"Datadis API not ready: {err}") from err

    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DatadisConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(hass: HomeAssistant, entry: DatadisConfigEntry) -> None:
    """Reload config entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
