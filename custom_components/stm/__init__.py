"""STM – Société de transport de Montréal integration."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import (
    DOMAIN, CONF_API_KEY, CONF_ENTRY_TYPE, CONF_STOP_ID,
    ENTRY_TYPE_METRO, ENTRY_TYPE_STOP,
)
from .coordinator import STMStopCoordinator, STMMetroCoordinator
from .gtfs_static import GTFSStaticManager

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]


def _copy_assets(hass: HomeAssistant) -> None:
    src = Path(__file__).parent
    dst = Path(hass.config.config_dir) / "www" / "stm"
    dst.mkdir(parents=True, exist_ok=True)
    for f in ("icon.png", "logo.png", "logo.svg"):
        if (src / f).exists():
            shutil.copy2(src / f, dst / f)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})
    await hass.async_add_executor_job(_copy_assets, hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    api_key    = entry.data[CONF_API_KEY]
    entry_type = entry.data.get(CONF_ENTRY_TYPE, ENTRY_TYPE_METRO)

    # ── GTFS static — one instance, lazy load ────────────────────────────────
    if "gtfs" not in hass.data[DOMAIN]:
        gtfs = GTFSStaticManager(hass.config.path(".storage", "stm"))
        hass.data[DOMAIN]["gtfs"] = gtfs
    hass.async_create_task(hass.data[DOMAIN]["gtfs"].async_ensure_loaded())

    # ── Assets ────────────────────────────────────────────────────────────────
    await hass.async_add_executor_job(_copy_assets, hass)

    # ── Coordinators ──────────────────────────────────────────────────────────
    if entry_type == ENTRY_TYPE_STOP:
        stop_id = entry.data.get(CONF_STOP_ID, "")

        # Create or reuse the shared stop coordinator
        if "stop_coordinator" not in hass.data[DOMAIN]:
            coord = STMStopCoordinator(hass, api_key)
            await coord.async_config_entry_first_refresh()
            hass.data[DOMAIN]["stop_coordinator"] = coord

        coord = hass.data[DOMAIN]["stop_coordinator"]

        # Register this stop so the coordinator only parses relevant data
        if stop_id:
            coord.watched_stops.add(stop_id)

        hass.data[DOMAIN][entry.entry_id] = coord

    elif entry_type == ENTRY_TYPE_METRO:
        coord = STMMetroCoordinator(hass, api_key)
        await coord.async_config_entry_first_refresh()
        hass.data[DOMAIN][entry.entry_id] = coord

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry_type = entry.data.get(CONF_ENTRY_TYPE)
        if entry_type == ENTRY_TYPE_STOP:
            stop_id = entry.data.get(CONF_STOP_ID, "")
            coord   = hass.data[DOMAIN].get("stop_coordinator")

            # Unregister stop
            if coord and stop_id:
                coord.watched_stops.discard(stop_id)

            # Remove shared coordinator only when no stop entries remain
            remaining = [
                e for e in hass.config_entries.async_entries(DOMAIN)
                if e.entry_id != entry.entry_id
                and e.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_STOP
            ]
            if not remaining:
                hass.data[DOMAIN].pop("stop_coordinator", None)

        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
