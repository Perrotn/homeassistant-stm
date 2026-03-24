"""Binary sensor platform — one per metro line (Normal / Perturbé)."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN, CONF_ENTRY_TYPE, ENTRY_TYPE_METRO,
    METRO_LINES, STATUS_NORMAL, STATUS_UNKNOWN,
)
from .coordinator import STMMetroCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    if entry.data.get(CONF_ENTRY_TYPE) != ENTRY_TYPE_METRO:
        return  # Only metro entry creates binary sensors

    coord = hass.data[DOMAIN][entry.entry_id]
    entities = [
        STMMetroLineBinarySensor(coord, line_key, line_info)
        for line_key, line_info in METRO_LINES.items()
    ]
    async_add_entities(entities, update_before_add=True)


class STMMetroLineBinarySensor(CoordinatorEntity[STMMetroCoordinator], BinarySensorEntity):
    """
    ON  = service perturbé ou interrompu
    OFF = service normal

    device_class = "problem" → HA affiche "Problème" / "OK"
    """
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator, line_key: str, line_info: dict) -> None:
        super().__init__(coordinator)
        self._line_key  = line_key
        self._line_info = line_info
        self._attr_unique_id = f"stm_metro_problem_{line_key}"
        self._attr_name      = f"STM Perturbation {line_info['name']}"

    @property
    def is_on(self) -> bool:
        """True = problem detected."""
        status = self._d().get("status", STATUS_UNKNOWN)
        return status not in (STATUS_NORMAL, STATUS_UNKNOWN)

    @property
    def icon(self) -> str:
        return "mdi:subway-alert-variant" if self.is_on else "mdi:subway-variant"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self._d()
        return {
            "statut":        d.get("status", STATUS_UNKNOWN),
            "message":       d.get("message", ""),
            "perturbations": d.get("perturbations", []),
            "avis_mineurs":  d.get("avis_mineurs", []),
            "couleur":       self._line_info["color"],
            "route_id":      self._line_info["route_id"],
        }

    def _d(self) -> dict:
        if not self.coordinator.data:
            return {}
        return self.coordinator.data.get("metro_status", {}).get(self._line_key, {})

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success
