"""Datadis integration."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .api import DatadisApiClient, DatadisApiError, DatadisCredentials
from .const import (
    CONF_CUPS,
    CONF_DISTRIBUTOR_CODE,
    CONF_PASSWORD,
    CONF_POINT_TYPE,
    CONF_QUERY_DAYS,
    CONF_RATE_LIMIT_COOLDOWN_HOURS,
    CONF_UPDATE_INTERVAL,
    CONF_USERNAME,
    DEFAULT_POINT_TYPE,
    DEFAULT_DISTRIBUTOR_CODE,
    DEFAULT_QUERY_DAYS,
    DEFAULT_RATE_LIMIT_COOLDOWN_HOURS,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import DatadisCoordinator

_LOGGER = logging.getLogger(__name__)


DatadisConfigEntry = ConfigEntry[DatadisCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: DatadisConfigEntry) -> bool:
    """Set up Datadis from a config entry."""
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    cups = entry.options.get(CONF_CUPS, entry.data[CONF_CUPS])
    distributor_code = entry.options.get(
        CONF_DISTRIBUTOR_CODE,
        entry.data.get(CONF_DISTRIBUTOR_CODE, DEFAULT_DISTRIBUTOR_CODE),
    )
    point_type = entry.options.get(
        CONF_POINT_TYPE,
        entry.data.get(CONF_POINT_TYPE, DEFAULT_POINT_TYPE),
    )

    credentials = DatadisCredentials(
        username=username,
        password=password,
    )

    client = DatadisApiClient(
        hass=hass,
        credentials=credentials,
        cups=cups,
        distributor_code=distributor_code,
        point_type=point_type,
    )

    update_interval = entry.options.get(
        CONF_UPDATE_INTERVAL,
        entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_MINUTES),
    )
    query_days = entry.options.get(
        CONF_QUERY_DAYS,
        entry.data.get(CONF_QUERY_DAYS, DEFAULT_QUERY_DAYS),
    )
    rate_limit_cooldown_hours = entry.options.get(
        CONF_RATE_LIMIT_COOLDOWN_HOURS,
        entry.data.get(
            CONF_RATE_LIMIT_COOLDOWN_HOURS, DEFAULT_RATE_LIMIT_COOLDOWN_HOURS
        ),
    )

    # Never keep credentials in options storage.
    if CONF_USERNAME in entry.options or CONF_PASSWORD in entry.options:
        sanitized_options = {
            key: value
            for key, value in entry.options.items()
            if key not in {CONF_USERNAME, CONF_PASSWORD}
        }
        hass.config_entries.async_update_entry(entry, options=sanitized_options)

    coordinator = DatadisCoordinator(
        hass=hass,
        client=client,
        name=f"datadis_{cups}",
        update_interval_minutes=update_interval,
        query_days=query_days,
        rate_limit_cooldown_hours=rate_limit_cooldown_hours,
    )

    try:
        async with asyncio.timeout(20):
            await coordinator.async_config_entry_first_refresh()
    except TimeoutError:
        _LOGGER.warning(
            "Datadis first refresh timed out during setup; continuing and refreshing in background"
        )
        hass.async_create_task(coordinator.async_refresh())
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
