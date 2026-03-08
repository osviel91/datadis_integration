"""Coordinator for Datadis integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
import homeassistant.util.dt as dt_util

from .api import (
    DatadisApiClient,
    DatadisApiError,
    DatadisAuthError,
    DatadisRateLimitError,
)
from .const import DEFAULT_QUERY_DAYS, DEFAULT_UPDATE_INTERVAL_MINUTES

_LOGGER = logging.getLogger(__name__)
_STORAGE_VERSION = 1
_STORAGE_KEY_PREFIX = "datadis_cache_"


@dataclass(slots=True)
class DatadisData:
    """Processed data for entities."""

    monthly_consumption_kwh: float | None
    monthly_consumption_is_fallback: bool
    data_period_start: datetime | None
    data_period_end: datetime | None
    yesterday_consumption_kwh: float | None
    latest_hour_consumption_kwh: float | None
    latest_measurement_at: datetime | None
    monthly_peak_power_kw: float | None
    last_successful_update: datetime | None
    next_allowed_query_at: datetime | None
    rate_limit_reached: bool


class DatadisCoordinator(DataUpdateCoordinator[DatadisData]):
    """Fetch Datadis data and prepare sensor-friendly values."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: DatadisApiClient,
        name: str,
        update_interval_minutes: int = DEFAULT_UPDATE_INTERVAL_MINUTES,
        query_days: int = DEFAULT_QUERY_DAYS,
        rate_limit_cooldown_hours: int = 24,
    ) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name=name,
            update_interval=timedelta(minutes=update_interval_minutes),
        )
        self.client = client
        self.query_days = query_days
        self.rate_limit_cooldown_hours = rate_limit_cooldown_hours
        self._last_consumption_rows: list[dict[str, Any]] = []
        self._last_max_power_rows: list[dict[str, Any]] = []
        self._next_consumption_try: datetime | None = None
        self._next_max_power_try: datetime | None = None
        self._last_successful_update: datetime | None = None
        self._forced_refresh = False
        self._cache_loaded = False
        self._store: Store[dict[str, Any]] = Store(
            hass, _STORAGE_VERSION, f"{_STORAGE_KEY_PREFIX}{name}"
        )

    async def async_force_refresh(self) -> None:
        """Force an immediate refresh, bypassing cooldown windows."""
        self._forced_refresh = True
        self._next_consumption_try = None
        self._next_max_power_try = None
        await self.async_request_refresh()

    async def _async_update_data(self) -> DatadisData:
        if not self._cache_loaded:
            await self._async_load_cache()

        now = dt_util.now()
        query_start = (now - timedelta(days=self.query_days)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        consumption_rows: list[dict[str, Any]] = []
        max_power_rows: list[dict[str, Any]] = []
        rate_limit_reached = False

        if (
            not self._forced_refresh
            and self._next_consumption_try
            and now < self._next_consumption_try
        ):
            consumption_rows = self._last_consumption_rows
            rate_limit_reached = True
        else:
            try:
                consumption_rows = await self.client.async_get_consumption_data(
                    start_date=query_start,
                    end_date=now,
                )
                self._last_consumption_rows = consumption_rows
                self._next_consumption_try = None
                self._last_successful_update = now
                await self._async_save_cache()
            except DatadisAuthError as err:
                raise ConfigEntryAuthFailed from err
            except DatadisRateLimitError as err:
                _LOGGER.debug("Datadis consumption rate-limited: %s", err)
                rate_limit_reached = True
                if self._last_consumption_rows:
                    self._next_consumption_try = now + timedelta(
                        hours=self.rate_limit_cooldown_hours
                    )
                    consumption_rows = self._last_consumption_rows
                else:
                    # Bootstrap attempt: try previous-month windows that may not be rate-limited.
                    bootstrap_rows = await self._async_try_bootstrap_consumption(now)
                    if bootstrap_rows:
                        consumption_rows = bootstrap_rows
                        self._last_consumption_rows = bootstrap_rows
                        self._next_consumption_try = None
                        self._last_successful_update = now
                        await self._async_save_cache()
                    else:
                        self._next_consumption_try = now + timedelta(
                            hours=self.rate_limit_cooldown_hours
                        )
                        _LOGGER.warning(
                            "Datadis rate-limited with no cached data yet; retrying after cooldown window"
                        )
                        consumption_rows = self._last_consumption_rows
            except DatadisApiError as err:
                if err.status == 500:
                    # Datadis sometimes fails on certain periods. Retry with narrower month windows.
                    month_windows = _fallback_month_windows(now)
                    month_err: DatadisApiError | None = None
                    for start, end in month_windows:
                        try:
                            consumption_rows = await self.client.async_get_consumption_data(
                                start_date=start,
                                end_date=end,
                            )
                            self._last_consumption_rows = consumption_rows
                            self._next_consumption_try = None
                            self._last_successful_update = now
                            await self._async_save_cache()
                            month_err = None
                            break
                        except DatadisApiError as window_err:
                            month_err = window_err

                    if month_err is not None:
                        _LOGGER.debug(
                            "Datadis consumption backend error, keeping last data: %s",
                            month_err,
                        )
                        if self._last_consumption_rows:
                            self._next_consumption_try = now + timedelta(
                                hours=self.rate_limit_cooldown_hours
                            )
                        else:
                            self._next_consumption_try = now + timedelta(minutes=15)
                            if not self.client.distributor_code:
                                _LOGGER.warning(
                                    "Datadis backend error with no cached data and empty distributor code; "
                                    "set Distributor Code in Controls and retrying in 15 minutes"
                                )
                            else:
                                _LOGGER.warning(
                                    "Datadis backend error with no cached data yet; retrying in 15 minutes"
                                )
                        consumption_rows = self._last_consumption_rows
                else:
                    _LOGGER.warning("Datadis consumption fetch failed: %s", err)
                    consumption_rows = self._last_consumption_rows

        if (
            not self._forced_refresh
            and self._next_max_power_try
            and now < self._next_max_power_try
        ):
            max_power_rows = self._last_max_power_rows
            rate_limit_reached = True
        else:
            try:
                max_power_rows = await self.client.async_get_max_power_data(
                    start_date=query_start,
                    end_date=now,
                )
                self._last_max_power_rows = max_power_rows
                self._next_max_power_try = None
                self._last_successful_update = now
                await self._async_save_cache()
            except DatadisAuthError as err:
                raise ConfigEntryAuthFailed from err
            except DatadisRateLimitError as err:
                _LOGGER.debug("Datadis max power rate-limited: %s", err)
                rate_limit_reached = True
                if self._last_max_power_rows:
                    self._next_max_power_try = now + timedelta(
                        hours=self.rate_limit_cooldown_hours
                    )
                else:
                    self._next_max_power_try = now + timedelta(
                        hours=self.rate_limit_cooldown_hours
                    )
                max_power_rows = self._last_max_power_rows
            except DatadisApiError as err:
                _LOGGER.debug("Datadis max power fetch failed: %s", err)
                max_power_rows = self._last_max_power_rows

        self._forced_refresh = False

        next_allowed_query_at = _earliest_datetime(
            self._next_consumption_try, self._next_max_power_try
        )
        return self._build_data(
            now,
            consumption_rows,
            max_power_rows,
            self._last_successful_update,
            next_allowed_query_at,
            rate_limit_reached,
        )

    async def _async_load_cache(self) -> None:
        """Load last known data from storage."""
        self._cache_loaded = True
        cached = await self._store.async_load()
        if not cached:
            return

        consumption_rows = cached.get("consumption_rows")
        max_power_rows = cached.get("max_power_rows")
        last_successful_update = cached.get("last_successful_update")

        if isinstance(consumption_rows, list):
            self._last_consumption_rows = [
                row for row in consumption_rows if isinstance(row, dict)
            ]
        if isinstance(max_power_rows, list):
            self._last_max_power_rows = [row for row in max_power_rows if isinstance(row, dict)]
        if isinstance(last_successful_update, str):
            parsed = _parse_datetime(last_successful_update)
            if parsed:
                self._last_successful_update = parsed

    async def _async_save_cache(self) -> None:
        """Persist last known data to storage."""
        payload: dict[str, Any] = {
            "consumption_rows": self._last_consumption_rows,
            "max_power_rows": self._last_max_power_rows,
            "last_successful_update": self._last_successful_update.isoformat()
            if self._last_successful_update
            else None,
        }
        await self._store.async_save(payload)

    async def _async_try_bootstrap_consumption(
        self, now: datetime
    ) -> list[dict[str, Any]]:
        """Try previous-month windows to bootstrap first data when current window is rate-limited."""
        windows = _fallback_month_windows(now)
        for start, end in windows[1:]:
            try:
                rows = await self.client.async_get_consumption_data(
                    start_date=start, end_date=end
                )
                if rows:
                    return rows
            except (DatadisApiError, DatadisRateLimitError):
                continue
        return []

    def _build_data(
        self,
        now: datetime,
        consumption_rows: list[dict[str, Any]],
        max_power_rows: list[dict[str, Any]],
        last_successful_update: datetime | None,
        next_allowed_query_at: datetime | None,
        rate_limit_reached: bool,
    ) -> DatadisData:
        monthly = 0.0
        total_window = 0.0
        has_current_month_data = False
        month_start_date = now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        ).date()
        yesterday = (now - timedelta(days=1)).date()
        yesterday_total = 0.0
        has_yesterday_data = False

        latest_value = None
        latest_time = None
        period_start = None
        period_end = None

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

            total_window += value
            if when and when.date() >= month_start_date:
                has_current_month_data = True
                monthly += value
            if when and when.date() == yesterday:
                has_yesterday_data = True
                yesterday_total += value

            if when and (latest_time is None or when > latest_time):
                latest_time = when
                latest_value = value
            if when and (period_start is None or when < period_start):
                period_start = when
            if when and (period_end is None or when > period_end):
                period_end = when

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

        monthly_value = round(monthly, 3) if has_current_month_data else None
        monthly_fallback = False
        if monthly_value is None and total_window > 0:
            monthly_value = round(total_window, 3)
            monthly_fallback = True

        return DatadisData(
            monthly_consumption_kwh=monthly_value,
            monthly_consumption_is_fallback=monthly_fallback,
            data_period_start=period_start,
            data_period_end=period_end,
            yesterday_consumption_kwh=round(yesterday_total, 3)
            if has_yesterday_data
            else None,
            latest_hour_consumption_kwh=round(latest_value, 3)
            if latest_value is not None
            else None,
            latest_measurement_at=latest_time,
            monthly_peak_power_kw=round(peak_power, 3) if peak_power is not None else None,
            last_successful_update=last_successful_update,
            next_allowed_query_at=next_allowed_query_at,
            rate_limit_reached=rate_limit_reached,
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
    try:
        return datetime.fromisoformat(value_str)
    except ValueError:
        pass

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


def _earliest_datetime(*values: datetime | None) -> datetime | None:
    valid = [value for value in values if value is not None]
    return min(valid) if valid else None


def _fallback_month_windows(now: datetime) -> list[tuple[datetime, datetime]]:
    """Return fallback windows: current month and two previous months."""
    windows: list[tuple[datetime, datetime]] = []

    current_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    windows.append((current_start, now))

    prev_end = current_start - timedelta(days=1)
    prev_start = prev_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    windows.append((prev_start, prev_end.replace(hour=23, minute=59, second=59)))

    prev2_end = prev_start - timedelta(days=1)
    prev2_start = prev2_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    windows.append((prev2_start, prev2_end.replace(hour=23, minute=59, second=59)))

    return windows
