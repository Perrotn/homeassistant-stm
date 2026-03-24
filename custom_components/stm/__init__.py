"""STM – Société de transport de Montréal integration."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import (
    DOMAIN, CONF_API_KEY, CONF_ENTRY_TYPE,
    ENTRY_TYPE_METRO, ENTRY_TYPE_STOP,
)
from .coordinator import STMStopCoordinator, STMMetroCoordinator
from .gtfs_static import GTFSStaticManager

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]


def _copy_assets_to_www(hass: HomeAssistant) -> None:
    """
    Copy icon.png and logo.png to config/www/stm/ so they are
    accessible at /local/stm/icon.png and /local/stm/logo.png.
    HA always serves the www folder at /local/ — no API needed.
    """
    src_dir = Path(__file__).parent
    dst_dir = Path(hass.config.config_dir) / "www" / "stm"
    dst_dir.mkdir(parents=True, exist_ok=True)

    for filename in ("icon.png", "logo.png", "logo.svg"):
        src = src_dir / filename
        dst = dst_dir / filename
        if src.exists():
            shutil.copy2(src, dst)
            _LOGGER.debug("STM: copied %s → %s", filename, dst)

    _LOGGER.info(
        "STM assets available at /local/stm/icon.png and /local/stm/logo.png"
    )


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})
    await hass.async_add_executor_job(_copy_assets_to_www, hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    # Copy assets in case async_setup wasn't called
    await hass.async_add_executor_job(_copy_assets_to_www, hass)

    api_key    = entry.data[CONF_API_KEY]
    entry_type = entry.data.get(CONF_ENTRY_TYPE, ENTRY_TYPE_METRO)

    # ── GTFS Static — shared singleton ───────────────────────────────────────
    if "gtfs" not in hass.data[DOMAIN]:
        storage_dir = hass.config.path(".storage", "stm")
        gtfs = GTFSStaticManager(storage_dir)
        hass.data[DOMAIN]["gtfs"] = gtfs
    hass.async_create_task(
        hass.data[DOMAIN]["gtfs"].async_ensure_loaded()
    )

    # ── Coordinators ──────────────────────────────────────────────────────────
    if entry_type == ENTRY_TYPE_STOP:
        if "stop_coordinator" not in hass.data[DOMAIN]:
            coord = STMStopCoordinator(hass, api_key)
            await coord.async_config_entry_first_refresh()
            hass.data[DOMAIN]["stop_coordinator"] = coord
        hass.data[DOMAIN][entry.entry_id] = hass.data[DOMAIN]["stop_coordinator"]

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
            remaining_stops = [
                e for e in hass.config_entries.async_entries(DOMAIN)
                if e.entry_id != entry.entry_id
                and e.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_STOP
            ]
            if not remaining_stops:
                hass.data[DOMAIN].pop("stop_coordinator", None)
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
