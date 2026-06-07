"""Datadis API client."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
import gzip
import json
import logging
from typing import Any

import aiohttp
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONSUMPTION_URL,
    MAX_POWER_URL,
    MEASUREMENT_TYPE_ELECTRICITY,
    SUPPLIES_URL,
    POINT_TYPE_SUPPLY_POINT,
    TOKEN_URL,
)

_LOGGER = logging.getLogger(__name__)
REQUEST_TIMEOUT_SECONDS = 12
MAX_FALLBACK_ATTEMPTS = 16
SUPPLY_TIMEOUT_SECONDS = 35
TOKEN_TIMEOUT_SECONDS = 20  # Token requests can be slower
MAX_RETRY_BACKOFF_SECONDS = 300  # 5 min max backoff
FRESH_DATA_MAX_LAG = timedelta(days=7)


class DatadisApiError(Exception):
    """Raised when Datadis API responds with an error."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class DatadisAuthError(DatadisApiError):
    """Raised when Datadis auth fails."""


class DatadisRateLimitError(DatadisApiError):
    """Raised when Datadis rate limits requests."""


@dataclass(slots=True)
class DatadisCredentials:
    """Datadis credentials."""

    username: str
    password: str


class DatadisApiClient:
    """Thin client for Datadis private API."""

    def __init__(
        self,
        hass,
        credentials: DatadisCredentials,
        cups: str,
        distributor_code: str,
        point_type: str,
    ) -> None:
        self._hass = hass
        self._session = async_get_clientsession(hass)
        self._credentials = credentials
        self._cups = cups
        self._distributor_code = distributor_code
        self._point_type = point_type
        self._access_token: str | None = None
        self._supply_resolved = False
        self._token_failures = 0  # Track consecutive token failures

    @property
    def distributor_code(self) -> str:
        """Return current distributor code."""
        return self._distributor_code

    async def async_validate_access(self) -> None:
        """Validate credentials and CUPS by requesting available supplies."""
        await self._async_resolve_supply()

        # Some Datadis accounts/distributors return broken gzip for contract-detail.
        # Supplies + CUPS match is enough for onboarding validation.

    async def async_get_consumption_data(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> list[dict[str, Any]]:
        """Return consumption data for a date range."""
        await self._async_try_resolve_supply()
        return await self._async_request_with_param_fallbacks(
            CONSUMPTION_URL,
            start_date,
            end_date,
        )

    async def async_get_max_power_data(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> list[dict[str, Any]]:
        """Return max power data for a date range."""
        await self._async_try_resolve_supply()
        return await self._async_request_with_param_fallbacks(
            MAX_POWER_URL,
            start_date,
            end_date,
        )

    async def _async_request_with_param_fallbacks(
        self, url: str, start_date: datetime, end_date: datetime
    ) -> list[dict[str, Any]]:
        """Try endpoint with alternative parameter/date formats for Datadis quirks."""
        attempts = _build_query_param_attempts(
            cups=self._cups,
            distributor_code=self._distributor_code,
            start_date=start_date,
            end_date=end_date,
            point_type=self._point_type,
        )
        last_err: DatadisApiError | None = None
        best_rows: list[dict[str, Any]] = []
        best_latest: datetime | None = None

        methods = ("get", "post")
        for method in methods:
            # POST fallback is only useful after a GET 500/400 path.
            if method == "post" and (last_err is None or last_err.status not in {400, 500}):
                break
            for idx, params in enumerate(attempts[:MAX_FALLBACK_ATTEMPTS], start=1):
                try:
                    data = await self._async_request(url, params, method=method)
                    rows = _extract_datadis_rows(data)
                    latest = _latest_row_datetime(rows)
                    if _is_recent_enough(latest, end_date):
                        return rows
                    if rows and (best_latest is None or (latest and latest > best_latest)):
                        best_rows = rows
                        best_latest = latest
                    if rows:
                        continue
                    return []
                except DatadisRateLimitError as err:
                    last_err = err
                    break
                except DatadisApiError as err:
                    last_err = err
                    if err.status not in {400, 500} or idx == len(attempts):
                        break
                    _LOGGER.debug(
                        "Datadis %s %s fallback %s/%s",
                        url,
                        method.upper(),
                        idx + 1,
                        len(attempts),
                    )

        if best_rows:
            _LOGGER.warning(
                "Datadis returned only stale/partial rows up to %s for %s",
                best_latest.isoformat() if best_latest else "unknown",
                url,
            )
            return best_rows
        if last_err is not None:
            raise last_err
        return []

    async def _async_get_token(self, force_refresh: bool = False) -> str:
        if self._access_token and not force_refresh:
            return self._access_token

        payload = {
            "username": self._credentials.username,
            "password": self._credentials.password,
        }

        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        }

        try:
            async with self._session.post(
                TOKEN_URL,
                data=payload,
                headers=headers,
                timeout=TOKEN_TIMEOUT_SECONDS,
                auto_decompress=False,
            ) as response:
                if response.status == 401:
                    self._token_failures += 1
                    raise DatadisAuthError("Authentication failed")
                if response.status >= 400:
                    text = await _async_read_response_text(response)
                    self._token_failures += 1
                    raise DatadisApiError(
                        f"Token request failed ({response.status}): {text}",
                        status=response.status,
                    )

                data = await _async_read_json_or_text(response)
        except asyncio.TimeoutError as err:
            self._token_failures += 1
            raise DatadisApiError("Token request timed out") from err
        except aiohttp.ClientError as err:
            self._token_failures += 1
            raise DatadisApiError(f"Token request connection error: {err}") from err

        self._token_failures = 0  # Reset on success

        access_token = None
        if isinstance(data, dict):
            access_token = (
                data.get("access_token")
                or data.get("accessToken")
                or data.get("token")
                or data.get("id_token")
            )
        elif isinstance(data, str):
            access_token = data

        if not access_token:
            self._token_failures += 1
            raise DatadisAuthError("Datadis response did not include accessToken")

        self._access_token = access_token
        return access_token

    async def _async_resolve_supply(self) -> None:
        """Resolve supply metadata and distributor code for the configured CUPS."""
        if self._supply_resolved:
            return

        last_error: DatadisApiError | None = None
        for distributor_candidate in _distributor_candidates(self._distributor_code):
            supply_params: dict[str, Any] = {}
            if distributor_candidate:
                supply_params["distributor_code"] = distributor_candidate

            try:
                supplies_data = await self._async_request(
                    SUPPLIES_URL,
                    supply_params,
                    method="get",
                    timeout_seconds=SUPPLY_TIMEOUT_SECONDS,
                )
            except DatadisApiError as err:
                last_error = err
                continue

            supplies = _extract_supply_rows(supplies_data)
            if supplies is None:
                continue

            matched_row = next(
                (
                    row
                    for row in supplies
                    if str(row.get("cups", "")).strip() == self._cups
                    or str(row.get("CUPS", "")).strip() == self._cups
                ),
                None,
            )
            if not matched_row:
                continue

            code = (
                matched_row.get("distributor_code")
                or matched_row.get("distributorCode")
                or matched_row.get("distributor")
                or distributor_candidate
            )
            if code:
                self._distributor_code = str(code).strip()
            self._supply_resolved = True
            return

        if last_error is not None:
            raise last_error
        # Do not hard fail when Datadis returns unexpected wrappers/empty rows.
        self._supply_resolved = True

    async def _async_try_resolve_supply(self) -> None:
        """Best-effort supply resolution; do not block data fetch on timeout/errors."""
        try:
            await self._async_resolve_supply()
        except DatadisApiError as err:
            _LOGGER.debug("Skipping supply resolution due to error: %s", err)
            # Keep operating with configured values/fallbacks.

    async def _async_request(
        self,
        url: str,
        body: dict[str, Any],
        method: str = "post",
        timeout_seconds: int = REQUEST_TIMEOUT_SECONDS,
    ) -> Any:
        token = await self._async_get_token()
        response_data = await self._async_call(
            url, body, token, method=method, timeout_seconds=timeout_seconds
        )

        if isinstance(response_data, dict) and response_data.get("cod"):  # API error
            if str(response_data.get("cod")) in {"401", "403"}:
                token = await self._async_get_token(force_refresh=True)
                response_data = await self._async_call(
                    url, body, token, method=method, timeout_seconds=timeout_seconds
                )
            else:
                message = response_data.get("message") or response_data
                raise DatadisApiError(f"Datadis API error: {message}")

        return response_data

    async def _async_call(
        self,
        url: str,
        body: dict[str, Any],
        token: str,
        method: str = "post",
        timeout_seconds: int = REQUEST_TIMEOUT_SECONDS,
    ) -> Any:
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        }

        request = self._session.get if method.lower() == "get" else self._session.post
        params = body if method.lower() == "get" else None
        data = body if method.lower() != "get" else None

        try:
            async with request(
                url,
                params=params,
                data=data,
                headers=headers,
                timeout=timeout_seconds,
                auto_decompress=False,
            ) as response:
                if response.status == 401:
                    _LOGGER.debug("Datadis token expired for %s", url)
                    return {"cod": "401", "message": "Unauthorized"}
                if response.status == 429:
                    text = await _async_read_response_text(response)
                    raise DatadisRateLimitError(
                        f"Request failed ({response.status}): {text}",
                        status=response.status,
                    )
                if response.status >= 400:
                    text = await _async_read_response_text(response)
                    raise DatadisApiError(
                        f"Request failed ({response.status}): {text}",
                        status=response.status,
                    )
                return await _async_read_json_or_text(response)
        except asyncio.TimeoutError as err:
            raise DatadisApiError(f"Request timed out: {url}") from err
        except aiohttp.ClientError as err:
            raise DatadisApiError(f"Request connection error: {err}") from err


async def _async_read_json_or_text(response) -> Any:
    """Read API response as JSON when possible, fallback to text.

    Some Datadis endpoints may send ``Content-Encoding: gzip`` with a plain
    response body. aiohttp's automatic decompression raises before callers can
    inspect that body, so requests use ``auto_decompress=False`` and this helper
    only decompresses when the bytes are actually gzip.
    """
    text = await _async_read_response_text(response)
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


async def _async_read_response_text(response) -> str:
    """Read response bytes defensively, handling broken gzip headers."""
    raw = await response.read()
    if not raw:
        return ""

    content_encoding = response.headers.get("Content-Encoding", "").lower()
    if "gzip" in content_encoding and raw.startswith(b"\x1f\x8b"):
        raw = gzip.decompress(raw)

    return raw.decode("utf-8", errors="replace").strip()


def _extract_supply_rows(supplies_data: Any) -> list[dict[str, Any]] | None:
    """Normalize Datadis supply list wrapper variants."""
    if isinstance(supplies_data, list):
        return [row for row in supplies_data if isinstance(row, dict)]
    if isinstance(supplies_data, dict):
        for key in ("supplies", "data", "items", "result"):
            candidate = supplies_data.get(key)
            if isinstance(candidate, list):
                return [row for row in candidate if isinstance(row, dict)]
    return None


def _extract_datadis_rows(data: Any) -> list[dict[str, Any]]:
    """Normalize Datadis endpoint wrapper variants into row dicts."""
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("data", "items", "result"):
            candidate = data.get(key)
            if isinstance(candidate, list):
                return [row for row in candidate if isinstance(row, dict)]
    return []


def _latest_row_datetime(rows: list[dict[str, Any]]) -> datetime | None:
    latest = None
    for row in rows:
        for key in ("datetime", "date", "timestamp", "hour"):
            value = row.get(key)
            if not value:
                continue
            parsed = _parse_datetime(value)
            if parsed is not None and (latest is None or parsed > latest):
                latest = parsed
            break
    return latest


def _is_recent_enough(latest: datetime | None, end_date: datetime) -> bool:
    """Datadis data is delayed, but successful polls should still be within a reasonable lag."""
    if latest is None:
        return False
    return latest.date() >= (end_date - FRESH_DATA_MAX_LAG).date()


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    value_str = str(value)
    for parser in (
        datetime.fromisoformat,
        lambda v: datetime.strptime(v, "%Y/%m/%d %H:%M"),
        lambda v: datetime.strptime(v, "%Y-%m-%d %H:%M:%S"),
        lambda v: datetime.strptime(v, "%Y-%m-%dT%H:%M:%S"),
        lambda v: datetime.strptime(v, "%Y/%m/%d"),
    ):
        try:
            return parser(value_str)
        except ValueError:
            continue
    return None


def _build_query_param_attempts(
    cups: str,
    distributor_code: str,
    start_date: datetime,
    end_date: datetime,
    point_type: str,
) -> list[dict[str, Any]]:
    """Build fallback request variants for Datadis query endpoints."""
    base_variants = []
    # Datadis commonly expects month-formatted ranges (YYYY/MM).
    # Some backends break when measurement/point params are present,
    # so we also generate minimal variants without them.
    for candidate_point_type in (point_type, POINT_TYPE_SUPPLY_POINT, "1"):
        base_variants.extend(
            [
                {
                    "start_date": start_date.strftime("%Y/%m"),
                    "end_date": end_date.strftime("%Y/%m"),
                    "measurement_type": MEASUREMENT_TYPE_ELECTRICITY,
                    "point_type": candidate_point_type,
                },
                {
                    "startDate": start_date.strftime("%Y/%m"),
                    "endDate": end_date.strftime("%Y/%m"),
                    "measurementType": MEASUREMENT_TYPE_ELECTRICITY,
                    "pointType": candidate_point_type,
                },
                {
                    "start_date": start_date.strftime("%Y/%m/%d"),
                    "end_date": end_date.strftime("%Y/%m/%d"),
                    "measurement_type": MEASUREMENT_TYPE_ELECTRICITY,
                    "point_type": candidate_point_type,
                },
                {
                    "startDate": start_date.strftime("%Y/%m/%d"),
                    "endDate": end_date.strftime("%Y/%m/%d"),
                    "measurementType": MEASUREMENT_TYPE_ELECTRICITY,
                    "pointType": candidate_point_type,
                },
            ]
        )

    # Minimal variants
    base_variants.extend(
        [
            {
                "start_date": start_date.strftime("%Y/%m"),
                "end_date": end_date.strftime("%Y/%m"),
            },
            {
                "startDate": start_date.strftime("%Y/%m"),
                "endDate": end_date.strftime("%Y/%m"),
            },
            {
                "start_date": start_date.strftime("%Y/%m/%d"),
                "end_date": end_date.strftime("%Y/%m/%d"),
            },
            {
                "startDate": start_date.strftime("%Y/%m/%d"),
                "endDate": end_date.strftime("%Y/%m/%d"),
            },
        ]
    )

    # Try previous month as well (helps with Datadis 24h restriction)
    for month_offset in (0, -1, -2):
        for candidate_point_type in (point_type, POINT_TYPE_SUPPLY_POINT, "1"):
            base_variants.append(
                {
                    "start_date": _shift_month(start_date, month_offset).strftime("%Y/%m"),
                    "end_date": _shift_month(end_date, month_offset).strftime("%Y/%m"),
                    "measurement_type": MEASUREMENT_TYPE_ELECTRICITY,
                    "point_type": candidate_point_type,
                },
            )

    distributor_candidates = _distributor_candidates(distributor_code)

    attempts: list[dict[str, Any]] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for variant in base_variants:
        for distributor_candidate in distributor_candidates:
            snake = {"cups": cups, **variant}
            camel = {"cups": cups, **variant}
            if distributor_candidate:
                snake["distributor_code"] = distributor_candidate
                camel["distributorCode"] = distributor_candidate
            for candidate in (snake, camel):
                key = tuple(sorted((k, str(v)) for k, v in candidate.items()))
                if key in seen:
                    continue
                seen.add(key)
                attempts.append(candidate)

    return attempts


def _shift_month(dt: datetime, offset: int) -> datetime:
    """Shift datetime by N months, clamping to valid days."""
    month = dt.month + offset
    year = dt.year
    while month > 12:
        month -= 12
        year += 1
    while month < 1:
        month += 12
        year -= 1
    import calendar
    max_day = calendar.monthrange(year, month)[1]
    day = min(dt.day, max_day)
    return dt.replace(year=year, month=month, day=day)


def _distributor_candidates(code: str) -> list[str]:
    """Return distributor fallback values."""
    normalized = (code or "").strip()
    candidates: list[str] = []
    for value in (
        normalized,
        normalized.lower(),
        normalized.upper(),
        "i-de",
        "I-DE",
        "",
    ):
        if value in candidates:
            continue
        candidates.append(value)
    return candidates
