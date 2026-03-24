"""Config flow for STM integration."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    DOMAIN, CONF_API_KEY, CONF_ENTRY_TYPE, CONF_STOP_ID,
    CONF_ROUTE_ID, CONF_MAX_DEPARTURES, DEFAULT_MAX_DEPARTURES,
    ENTRY_TYPE_METRO, ENTRY_TYPE_STOP,
    STM_API_SERVICE_STATUS, STM_SERVICE_API_HEADER, STM_API_ORIGIN_HEADER,
)

_LOGGER = logging.getLogger(__name__)


async def _validate_api_key(api_key: str) -> bool:
    headers = {STM_SERVICE_API_HEADER: api_key, "Origin": STM_API_ORIGIN_HEADER}
    async with aiohttp.ClientSession() as session:
        async with session.get(
            STM_API_SERVICE_STATUS, headers=headers,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            return resp.status == 200


class STMConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    # ── Step 1: API key ───────────────────────────────────────────────────────
    async def async_step_user(self, user_input=None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            try:
                if not await _validate_api_key(api_key):
                    errors["base"] = "invalid_auth"
                else:
                    self._api_key = api_key
                    return await self.async_step_type()
            except aiohttp.ClientError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error validating API key")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_API_KEY): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
            }),
            errors=errors,
            description_placeholders={"portal_url": "https://portail.developpeurs.stm.info/apihub"},
        )

    # ── Step 2: metro or stop ─────────────────────────────────────────────────
    async def async_step_type(self, user_input=None) -> FlowResult:
        if user_input is not None:
            if user_input[CONF_ENTRY_TYPE] == ENTRY_TYPE_METRO:
                await self.async_set_unique_id("stm_metro_status")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="STM – État du métro",
                    data={CONF_API_KEY: self._api_key, CONF_ENTRY_TYPE: ENTRY_TYPE_METRO},
                )
            else:
                return await self.async_step_stop()

        return self.async_show_form(
            step_id="type",
            data_schema=vol.Schema({
                vol.Required(CONF_ENTRY_TYPE, default=ENTRY_TYPE_METRO): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": ENTRY_TYPE_METRO, "label": "🚇 Metro service status (4 lines)"},
                            {"value": ENTRY_TYPE_STOP,  "label": "🚌 Next departures at a stop"},
                        ],
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
            }),
        )

    # ── Step 3: stop config ───────────────────────────────────────────────────
    async def async_step_stop(self, user_input=None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            stop_id  = user_input.get(CONF_STOP_ID, "").strip()
            route_id = user_input.get(CONF_ROUTE_ID, "").strip()
            max_dep  = int(user_input.get(CONF_MAX_DEPARTURES, DEFAULT_MAX_DEPARTURES))

            if not stop_id:
                errors[CONF_STOP_ID] = "stop_required"
            else:
                # Check if GTFS is loaded and can validate the stop
                gtfs = self.hass.data.get(DOMAIN, {}).get("gtfs")
                if gtfs and gtfs._loaded and not gtfs.stop_exists(stop_id):
                    errors[CONF_STOP_ID] = "stop_not_found"
                else:
                    await self.async_set_unique_id(f"stm_stop_{stop_id}_{route_id}")
                    self._abort_if_unique_id_configured()
                    label = f"STM – Arrêt {stop_id}"
                    if route_id:
                        label += f" / Ligne {route_id}"
                    return self.async_create_entry(
                        title=label,
                        data={
                            CONF_API_KEY:        self._api_key,
                            CONF_ENTRY_TYPE:     ENTRY_TYPE_STOP,
                            CONF_STOP_ID:        stop_id,
                            CONF_ROUTE_ID:       route_id,
                            CONF_MAX_DEPARTURES: max_dep,
                        },
                    )

        return self.async_show_form(
            step_id="stop",
            data_schema=vol.Schema({
                vol.Required(CONF_STOP_ID): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                ),
                vol.Optional(CONF_ROUTE_ID, default=""): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                ),
                vol.Optional(CONF_MAX_DEPARTURES, default=DEFAULT_MAX_DEPARTURES): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=10, mode=selector.NumberSelectorMode.BOX)
                ),
            }),
            errors=errors,
            description_placeholders={"stop_search_url": "https://www.stm.info/fr/infos/reseaux/bus"},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return STMOptionsFlow(config_entry)


class STMOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, entry) -> None:
        self._entry = entry

    async def async_step_init(self, user_input=None) -> FlowResult:
        if self._entry.data.get(CONF_ENTRY_TYPE) != ENTRY_TYPE_STOP:
            return self.async_abort(reason="no_options")
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        current = self._entry.data
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_STOP_ID, default=current.get(CONF_STOP_ID, "")): str,
                vol.Optional(CONF_ROUTE_ID, default=current.get(CONF_ROUTE_ID, "")): str,
                vol.Optional(
                    CONF_MAX_DEPARTURES,
                    default=current.get(CONF_MAX_DEPARTURES, DEFAULT_MAX_DEPARTURES),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=10, mode=selector.NumberSelectorMode.BOX)
                ),
            }),
        )
