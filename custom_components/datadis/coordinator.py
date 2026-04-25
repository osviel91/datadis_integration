"""Coordinator for Datadis integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import calendar
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
_STORAGE_VERSION = 1  # Keep at 1 to avoid migration issues
_STORAGE_KEY_PREFIX = "datadis_cache_"
_CACHE_MAX_AGE_HOURS = 48  # Expire cached data older than 48h
_BOOTSTRAP_BACKOFF_MINUTES = 5  # Initial backoff for bootstrap
_BOOTSTRAP_MAX_BACKOFF_MINUTES = 120  # Max backoff after repeated failures
_TTL_STORAGE_KEY = "_cache_ttl"  # Internal key for cache timestamp


@dataclass(slots=True)
class DatadisData:
    """Processed data for entities."""

    monthly_consumption_kwh: float | None
    monthly_consumption_is_fallback: bool
    data_period_start: datetime | None
    data_period_end: datetime | None
    daily_consumption_kwh: float | None
    daily_consumption_date: datetime | None
    yesterday_consumption_kwh: float | None
    latest_hour_consumption_kwh: float | None
    latest_measurement_at: datetime | None
    monthly_peak_power_kw: float | None
    last_successful_update: datetime | None
    next_allowed_query_at: datetime | None
    rate_limit_reached: bool
    days_with_data_this_month: int | None
    current_month_daily_average_kwh: float | None
    projected_month_consumption_kwh: float | None
    highest_daily_consumption_this_month_kwh: float | None


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
        self._consumption_backoff_count = 0  # Progressive backoff counter
        self._max_power_backoff_count = 0
        self._store: Store[dict[str, Any]] = Store(
            hass, _STORAGE_VERSION, f"{_STORAGE_KEY_PREFIX}{name}"
        )

    async def async_force_refresh(self) -> None:
        """Force an immediate refresh, bypassing cooldown windows."""
        self._forced_refresh = True
        self._next_consumption_try = None
        self._next_max_power_try = None
        self._consumption_backoff_count = 0
        self._max_power_backoff_count = 0
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

        # --- Consumption ---
        consumption_rows, consumption_rate_limited = await self._async_fetch_consumption(now, query_start)
        if consumption_rate_limited:
            rate_limit_reached = True

        # --- Max Power ---
        max_power_rows, max_power_rate_limited = await self._async_fetch_max_power(now, query_start)
        if max_power_rate_limited:
            rate_limit_reached = True

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

    async def _async_fetch_consumption(
        self, now: datetime, query_start: datetime
    ) -> tuple[list[dict[str, Any]], bool]:
        """Fetch consumption with exponential backoff on rate limits."""
        rate_limited = False

        if (
            not self._forced_refresh
            and self._next_consumption_try
            and now < self._next_consumption_try
        ):
            return self._last_consumption_rows, True

        try:
            rows = await self.client.async_get_consumption_data(
                start_date=query_start,
                end_date=now,
            )
            self._last_consumption_rows = rows
            self._next_consumption_try = None
            self._last_successful_update = now
            self._consumption_backoff_count = 0  # Reset on success
            await self._async_save_cache()
            return rows, False

        except DatadisAuthError as err:
            raise ConfigEntryAuthFailed from err

        except DatadisRateLimitError as err:
            _LOGGER.debug("Datadis consumption rate-limited: %s", err)
            rate_limited = True
            self._consumption_backoff_count += 1

            if self._last_consumption_rows:
                # Use cached data with progressive backoff
                self._next_consumption_try = now + self._compute_backoff(
                    self._consumption_backoff_count
                )
                return self._last_consumption_rows, True

            # No cached data - try bootstrap with previous months
            bootstrap_rows = await self._async_try_bootstrap_consumption(now)
            if bootstrap_rows:
                self._last_consumption_rows = bootstrap_rows
                self._next_consumption_try = None
                self._last_successful_update = now
                self._consumption_backoff_count = 0
                await self._async_save_cache()
                return bootstrap_rows, False

            self._next_consumption_try = now + self._compute_backoff(
                self._consumption_backoff_count, max_hours=self.rate_limit_cooldown_hours
            )
            _LOGGER.warning(
                "Datadis rate-limited with no cached data; retry in %s",
                self._next_consumption_try - now,
            )
            return self._last_consumption_rows, True

        except DatadisApiError as err:
            if err.status == 500:
                return await self._async_fetch_consumption_with_month_fallback(now, err)

            _LOGGER.warning("Datadis consumption fetch failed: %s", err)
            return self._last_consumption_rows, rate_limited

    async def _async_fetch_consumption_with_month_fallback(
        self, now: datetime, original_err: DatadisApiError
    ) -> tuple[list[dict[str, Any]], bool]:
        """Retry consumption fetch with narrower month windows on backend errors."""
        month_windows = _fallback_month_windows(now)
        month_err: DatadisApiError | None = None

        for start, end in month_windows:
            try:
                rows = await self.client.async_get_consumption_data(
                    start_date=start,
                    end_date=end,
                )
                self._last_consumption_rows = rows
                self._next_consumption_try = None
                self._last_successful_update = now
                self._consumption_backoff_count = 0
                await self._async_save_cache()
                return rows, False
            except DatadisApiError as window_err:
                month_err = window_err

        if month_err is not None:
            _LOGGER.debug(
                "Datadis consumption backend error, keeping last data: %s",
                month_err,
            )
            if self._last_consumption_rows:
                self._next_consumption_try = now + timedelta(
                    hours=max(1, self.rate_limit_cooldown_hours)
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
        return self._last_consumption_rows, False

    async def _async_fetch_max_power(
        self, now: datetime, query_start: datetime
    ) -> tuple[list[dict[str, Any]], bool]:
        """Fetch max power with exponential backoff on rate limits."""
        rate_limited = False

        if (
            not self._forced_refresh
            and self._next_max_power_try
            and now < self._next_max_power_try
        ):
            return self._last_max_power_rows, True

        try:
            rows = await self.client.async_get_max_power_data(
                start_date=query_start,
                end_date=now,
            )
            self._last_max_power_rows = rows
            self._next_max_power_try = None
            self._last_successful_update = now
            self._max_power_backoff_count = 0
            await self._async_save_cache()
            return rows, False

        except DatadisAuthError as err:
            raise ConfigEntryAuthFailed from err

        except DatadisRateLimitError as err:
            _LOGGER.debug("Datadis max power rate-limited: %s", err)
            rate_limited = True
            self._max_power_backoff_count += 1
            self._next_max_power_try = now + self._compute_backoff(
                self._max_power_backoff_count, max_hours=self.rate_limit_cooldown_hours
            )
            return self._last_max_power_rows, True

        except DatadisApiError as err:
            _LOGGER.debug("Datadis max power fetch failed: %s", err)
            return self._last_max_power_rows, rate_limited

    def _compute_backoff(
        self, attempt_count: int, max_hours: int | None = None
    ) -> timedelta:
        """Compute progressive backoff delay. Starts small, grows with retries."""
        minutes = _BOOTSTRAP_BACKOFF_MINUTES * (2 ** (attempt_count - 1))
        minutes = min(minutes, _BOOTSTRAP_MAX_BACKOFF_MINUTES)

        if max_hours is not None:
            minutes = min(minutes, max_hours * 60)

        return timedelta(minutes=minutes)

    async def _async_load_cache(self) -> None:
        """Load last known data from storage, respecting TTL."""
        self._cache_loaded = True
        cached = await self._store.async_load()
        if not cached:
            return

        # Check cache TTL
        cache_timestamp = cached.get(_TTL_STORAGE_KEY)
        if cache_timestamp:
            try:
                cached_time = _parse_datetime(cache_timestamp)
                if cached_time and (dt_util.now() - cached_time) > timedelta(
                    hours=_CACHE_MAX_AGE_HOURS
                ):
                    _LOGGER.debug("Datadis cache expired, ignoring stale data")
                    return
            except (ValueError, TypeError):
                pass  # Corrupt timestamp, still try to use data

        consumption_rows = cached.get("consumption_rows")
        max_power_rows = cached.get("max_power_rows")
        last_successful_update = cached.get("last_successful_update")

        if isinstance(consumption_rows, list):
            self._last_consumption_rows = [
                row for row in consumption_rows if isinstance(row, dict)
            ]
        if isinstance(max_power_rows, list):
            self._last_max_power_rows = [
                row for row in max_power_rows if isinstance(row, dict)
            ]
        if isinstance(last_successful_update, str):
            parsed = _parse_datetime(last_successful_update)
            if parsed:
                self._last_successful_update = parsed

    async def _async_save_cache(self) -> None:
        """Persist last known data to storage with TTL timestamp."""
        payload: dict[str, Any] = {
            "consumption_rows": self._last_consumption_rows,
            "max_power_rows": self._last_max_power_rows,
            _TTL_STORAGE_KEY: dt_util.now().isoformat(),
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
        daily_totals: dict[datetime.date, float] = {}

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
            if when:
                daily_totals[when.date()] = daily_totals.get(when.date(), 0.0) + value
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

        days_with_data_this_month = sum(
            1 for day in daily_totals if day >= month_start_date
        )

        # Compute highest daily consumption in current month
        highest_daily_consumption_this_month = None
        if daily_totals:
            current_month_totals = [
                daily_totals[day]
                for day in daily_totals
                if day >= month_start_date
            ]
            if current_month_totals:
                highest_daily_consumption_this_month = round(
                    max(current_month_totals), 3
                )

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

        daily_consumption_date = max(daily_totals) if daily_totals else None
        daily_consumption_kwh = (
            round(daily_totals[daily_consumption_date], 3)
            if daily_consumption_date is not None
            else None
        )
        daily_consumption_datetime = (
            datetime.combine(daily_consumption_date, datetime.min.time())
            if daily_consumption_date is not None
            else None
        )

        resolved_yesterday_consumption = (
            round(yesterday_total, 3) if has_yesterday_data else None
        )
        if (
            resolved_yesterday_consumption is None
            and daily_consumption_date is not None
            and daily_consumption_date == yesterday
        ):
            resolved_yesterday_consumption = daily_consumption_kwh

        days_with_data_value = (
            days_with_data_this_month if days_with_data_this_month > 0 else None
        )

        # Compute current month daily average
        current_month_daily_average = None
        if (
            monthly_value is not None
            and days_with_data_value is not None
            and days_with_data_value > 0
        ):
            current_month_daily_average = round(
                monthly_value / days_with_data_value, 3
            )

        # Compute projected month consumption
        projected_month_consumption = None
        if current_month_daily_average is not None:
            days_in_month = calendar.monthrange(now.year, now.month)[1]
            projected_month_consumption = round(
                current_month_daily_average * days_in_month, 3
            )

        return DatadisData(
            monthly_consumption_kwh=monthly_value,
            monthly_consumption_is_fallback=monthly_fallback,
            data_period_start=period_start,
            data_period_end=period_end,
            daily_consumption_kwh=daily_consumption_kwh,
            daily_consumption_date=daily_consumption_datetime,
            yesterday_consumption_kwh=resolved_yesterday_consumption,
            latest_hour_consumption_kwh=round(latest_value, 3)
            if latest_value is not None
            else None,
            latest_measurement_at=latest_time,
            monthly_peak_power_kw=round(peak_power, 3) if peak_power is not None else None,
            last_successful_update=last_successful_update,
            next_allowed_query_at=next_allowed_query_at,
            rate_limit_reached=rate_limit_reached,
            days_with_data_this_month=days_with_data_value,
            current_month_daily_average_kwh=current_month_daily_average,
            projected_month_consumption_kwh=projected_month_consumption,
            highest_daily_consumption_this_month_kwh=highest_daily_consumption_this_month,
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
