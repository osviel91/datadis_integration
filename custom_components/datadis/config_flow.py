"""Config flow for Datadis integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant
from homeassistant.helpers.selector import NumberSelector, NumberSelectorConfig

from .api import DatadisApiClient, DatadisApiError, DatadisAuthError, DatadisCredentials
from .const import (
    CONF_DISTRIBUTOR_CODE,
    CONF_QUERY_DAYS,
    CONF_UPDATE_INTERVAL,
    DEFAULT_DISTRIBUTOR_CODE,
    DEFAULT_QUERY_DAYS,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    DOMAIN,
    MAX_QUERY_DAYS,
    MAX_UPDATE_INTERVAL_MINUTES,
    MIN_QUERY_DAYS,
    MIN_UPDATE_INTERVAL_MINUTES,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("username"): str,
        vol.Required("password"): str,
        vol.Required("cups"): str,
        vol.Optional(CONF_DISTRIBUTOR_CODE, default=DEFAULT_DISTRIBUTOR_CODE): str,
        vol.Optional(
            CONF_UPDATE_INTERVAL, default=DEFAULT_UPDATE_INTERVAL_MINUTES
        ): NumberSelector(
            NumberSelectorConfig(
                min=MIN_UPDATE_INTERVAL_MINUTES,
                max=MAX_UPDATE_INTERVAL_MINUTES,
                step=5,
                mode="box",
            )
        ),
        vol.Optional(CONF_QUERY_DAYS, default=DEFAULT_QUERY_DAYS): NumberSelector(
            NumberSelectorConfig(
                min=MIN_QUERY_DAYS,
                max=MAX_QUERY_DAYS,
                step=1,
                mode="box",
            )
        ),
    }
)


async def _validate_input(hass: HomeAssistant, data: dict[str, Any]) -> None:
    """Validate user data by calling Datadis contract endpoint."""
    credentials = DatadisCredentials(
        username=data["username"].strip(),
        password=data["password"],
    )

    client = DatadisApiClient(
        hass=hass,
        credentials=credentials,
        cups=data["cups"].strip(),
        distributor_code=data[CONF_DISTRIBUTOR_CODE].strip(),
    )
    await client.async_validate_access()


class DatadisConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle Datadis config flow."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            normalized_input = {
                **user_input,
                "username": user_input["username"].strip(),
                "cups": user_input["cups"].strip(),
                CONF_DISTRIBUTOR_CODE: user_input[CONF_DISTRIBUTOR_CODE].strip(),
                CONF_UPDATE_INTERVAL: int(user_input[CONF_UPDATE_INTERVAL]),
                CONF_QUERY_DAYS: int(user_input[CONF_QUERY_DAYS]),
            }

            await self.async_set_unique_id(normalized_input["cups"])
            self._abort_if_unique_id_configured()

            try:
                await _validate_input(self.hass, normalized_input)
            except DatadisAuthError:
                errors["base"] = "invalid_auth"
            except DatadisApiError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected Datadis config flow error")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=f"Datadis {normalized_input['cups']}",
                    data=normalized_input,
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        return DatadisOptionsFlow(config_entry)


class DatadisOptionsFlow(OptionsFlow):
    """Handle Datadis options."""

    def __init__(self, config_entry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    CONF_UPDATE_INTERVAL: int(user_input[CONF_UPDATE_INTERVAL]),
                    CONF_DISTRIBUTOR_CODE: user_input[CONF_DISTRIBUTOR_CODE].strip(),
                    CONF_QUERY_DAYS: int(user_input[CONF_QUERY_DAYS]),
                },
            )

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_DISTRIBUTOR_CODE,
                    default=self._config_entry.options.get(
                        CONF_DISTRIBUTOR_CODE,
                        self._config_entry.data.get(
                            CONF_DISTRIBUTOR_CODE, DEFAULT_DISTRIBUTOR_CODE
                        ),
                    ),
                ): str,
                vol.Optional(
                    CONF_UPDATE_INTERVAL,
                    default=self._config_entry.options.get(
                        CONF_UPDATE_INTERVAL,
                        self._config_entry.data.get(
                            CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_MINUTES
                        ),
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_UPDATE_INTERVAL_MINUTES,
                        max=MAX_UPDATE_INTERVAL_MINUTES,
                        step=5,
                        mode="box",
                    )
                ),
                vol.Optional(
                    CONF_QUERY_DAYS,
                    default=self._config_entry.options.get(
                        CONF_QUERY_DAYS,
                        self._config_entry.data.get(
                            CONF_QUERY_DAYS, DEFAULT_QUERY_DAYS
                        ),
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_QUERY_DAYS,
                        max=MAX_QUERY_DAYS,
                        step=1,
                        mode="box",
                    )
                ),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
