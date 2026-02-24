"""Coordinator for Datadis integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
import homeassistant.util.dt as dt_util

from .api import DatadisApiClient, DatadisApiError, DatadisAuthError
from .const import DEFAULT_QUERY_DAYS, DEFAULT_UPDATE_INTERVAL_MINUTES

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class DatadisData:
    """Processed data for entities."""

    monthly_consumption_kwh: float
    yesterday_consumption_kwh: float | None
    latest_hour_consumption_kwh: float | None
    latest_measurement_at: datetime | None
    monthly_peak_power_kw: float | None


class DatadisCoordinator(DataUpdateCoordinator[DatadisData]):
    """Fetch Datadis data and prepare sensor-friendly values."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: DatadisApiClient,
        name: str,
        update_interval_minutes: int = DEFAULT_UPDATE_INTERVAL_MINUTES,
        query_days: int = DEFAULT_QUERY_DAYS,
    ) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name=name,
            update_interval=timedelta(minutes=update_interval_minutes),
        )
        self.client = client
        self.query_days = query_days

    async def _async_update_data(self) -> DatadisData:
        now = dt_util.now()
        query_start = (now - timedelta(days=self.query_days)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        consumption_rows: list[dict[str, Any]] = []
        max_power_rows: list[dict[str, Any]] = []

        try:
            consumption_rows = await self.client.async_get_consumption_data(
                start_date=query_start,
                end_date=now,
            )
        except DatadisAuthError as err:
            raise ConfigEntryAuthFailed from err
        except DatadisApiError as err:
            _LOGGER.warning("Datadis consumption fetch failed: %s", err)

        try:
            max_power_rows = await self.client.async_get_max_power_data(
                start_date=query_start,
                end_date=now,
            )
        except DatadisAuthError as err:
            raise ConfigEntryAuthFailed from err
        except DatadisApiError as err:
            _LOGGER.debug("Datadis max power fetch failed: %s", err)

        return self._build_data(now, consumption_rows, max_power_rows)

    def _build_data(
        self,
        now: datetime,
        consumption_rows: list[dict[str, Any]],
        max_power_rows: list[dict[str, Any]],
    ) -> DatadisData:
        monthly = 0.0
        month_start_date = now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        ).date()
        yesterday = (now - timedelta(days=1)).date()
        yesterday_total = 0.0
        has_yesterday_data = False

        latest_value = None
        latest_time = None

        for row in consumption_rows or []:
            value = _to_float(
                row.get("consumptionKWh")
                or row.get("consumption_kwh")
                or row.get("consumption")
                or row.get("value")
            )
            when = _parse_datetime(
                row.get("datetime")
                or row.get("date")
                or row.get("timestamp")
                or row.get("hour")
            )
            if value is None:
                continue

            if when and when.date() >= month_start_date:
                monthly += value
            if when and when.date() == yesterday:
                has_yesterday_data = True
                yesterday_total += value

            if when and (latest_time is None or when > latest_time):
                latest_time = when
                latest_value = value

        peak_power = None
        for row in max_power_rows or []:
            value = _to_float(
                row.get("maxPower")
                or row.get("max_power")
                or row.get("power")
                or row.get("value")
            )
            if value is None:
                continue
            if peak_power is None or value > peak_power:
                peak_power = value

        return DatadisData(
            monthly_consumption_kwh=round(monthly, 3),
            yesterday_consumption_kwh=round(yesterday_total, 3)
            if has_yesterday_data
            else None,
            latest_hour_consumption_kwh=round(latest_value, 3)
            if latest_value is not None
            else None,
            latest_measurement_at=latest_time,
            monthly_peak_power_kw=round(peak_power, 3) if peak_power is not None else None,
        )


def _to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None

    if isinstance(value, datetime):
        return value

    value_str = str(value)
    patterns = (
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y/%m/%d",
    )

    for pattern in patterns:
        try:
            return datetime.strptime(value_str, pattern)
        except ValueError:
            continue

    return None
