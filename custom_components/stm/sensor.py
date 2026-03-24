"""Sensor platform — exposes all available STM data fields."""
from __future__ import annotations

import logging
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN, CONF_ENTRY_TYPE, CONF_STOP_ID, CONF_ROUTE_ID,
    CONF_MAX_DEPARTURES, DEFAULT_MAX_DEPARTURES,
    ENTRY_TYPE_METRO, ENTRY_TYPE_STOP,
    METRO_LINES, STM_TIMEZONE,
    STATUS_NORMAL, STATUS_PERTURBE, STATUS_INTERROMPU, STATUS_UNKNOWN,
    ATTR_DEPARTURES,
)
from .coordinator import STMStopCoordinator, STMMetroCoordinator

_LOGGER = logging.getLogger(__name__)
_MTL_TZ = ZoneInfo(STM_TIMEZONE)

_STATUS_ICON = {
    STATUS_NORMAL:     "mdi:subway-variant",
    STATUS_PERTURBE:   "mdi:alert-outline",
    STATUS_INTERROMPU: "mdi:subway-alert-variant",
    STATUS_UNKNOWN:    "mdi:help-circle-outline",
}

_DIRECTION_LABEL = {
    "N": "North", "S": "South", "E": "East", "W": "West",
    "North": "North", "South": "South", "East": "East", "West": "West",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coord      = hass.data[DOMAIN][entry.entry_id]
    gtfs       = hass.data[DOMAIN].get("gtfs")
    entry_type = entry.data.get(CONF_ENTRY_TYPE, ENTRY_TYPE_METRO)
    entities: list[SensorEntity] = []

    if entry_type == ENTRY_TYPE_METRO:
        for line_key, line_info in METRO_LINES.items():
            entities.append(STMMetroLineSensor(coord, line_key, line_info))
        entities.append(STMAlertSensor(coord))

    elif entry_type == ENTRY_TYPE_STOP:
        stop_id  = entry.data[CONF_STOP_ID]
        route_id = entry.data.get(CONF_ROUTE_ID, "")
        max_dep  = int(entry.data.get(CONF_MAX_DEPARTURES, DEFAULT_MAX_DEPARTURES))
        entities.append(STMDepartureSensor(coord, entry, stop_id, route_id, max_dep, gtfs))

    async_add_entities(entities, update_before_add=True)


# ─────────────────────────────────────────────────────────────────────────────
# Metro line status sensor
# ─────────────────────────────────────────────────────────────────────────────

class STMMetroLineSensor(CoordinatorEntity[STMMetroCoordinator], SensorEntity):

    def __init__(self, coordinator, line_key: str, line_info: dict) -> None:
        super().__init__(coordinator)
        self._line_key  = line_key
        self._line_info = line_info
        self._attr_unique_id = f"stm_metro_{line_key}"
        self._attr_name      = f"STM Metro {line_info['name']}"

    @property
    def native_value(self) -> str:
        return self._d().get("status", STATUS_UNKNOWN)

    @property
    def icon(self) -> str:
        return _STATUS_ICON.get(self.native_value, "mdi:subway-variant")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d    = self._d()
        data = self.coordinator.data or {}
        status = d.get("status", STATUS_UNKNOWN)

        attrs: dict[str, Any] = {
            # ── Identification ────────────────────────────────────────────────
            "route_id":        self._line_info["route_id"],
            "color":         self._line_info["color"],
            # ── Status ────────────────────────────────────────────────────────
            "message":         d.get("message", ""),
            "message_en":      d.get("message_en", ""),
            # ── Minor notices (access closures, elevators…) ───────────────────
            "minor_notices":    d.get("minor_notices", []),
            "nb_minor_notices": len(d.get("minor_notices", [])),
            # ── Metadata ──────────────────────────────────────────────────────
            "last_api_update": data.get("api_timestamp"),
            "last_update":      data.get("last_update", ""),
        }

        # Only show disruption details when there IS a disruption
        if status not in (STATUS_NORMAL, STATUS_UNKNOWN):
            perturbations = d.get("disruptions", [])
            directions    = d.get("directions", [])
            arrets_touches = d.get("affected_stops", [])

            attrs.update({
                "disruptions":      perturbations,
                "nb_disruptions":   len(perturbations),
                "disruption_start": d.get("debut"),
                "expected_resumption":     d.get("expected_resumption", "Indefinite duration"),
                "resumption_time":         d.get("resumption_time"),
                "affected_directions": [
                    _DIRECTION_LABEL.get(dr, dr) for dr in directions
                ],
                "affected_stops":     arrets_touches,
                "nb_affected_stops":  len(arrets_touches),
            })

        return attrs

    def _d(self) -> dict:
        if not self.coordinator.data:
            return {}
        return self.coordinator.data.get("metro_status", {}).get(self._line_key, {})

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success


# ─────────────────────────────────────────────────────────────────────────────
# Metro alert summary sensor
# ─────────────────────────────────────────────────────────────────────────────

class STMAlertSensor(CoordinatorEntity[STMMetroCoordinator], SensorEntity):
    _attr_unique_id                  = "stm_metro_alertes"
    _attr_name                       = "STM Metro alerts"
    _attr_icon                       = "mdi:alert-circle-outline"
    _attr_native_unit_of_measurement = "disruptions"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)

    @property
    def native_value(self) -> int:
        return len(self._disrupted())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}

        # All disrupted lines with full detail
        perturbations = []
        for k, d in self._disrupted():
            entry: dict[str, Any] = {
                "ligne":         METRO_LINES[k]["name"],
                "route_id":      METRO_LINES[k]["route_id"],
                "color":       METRO_LINES[k]["color"],
                "status":        d.get("status", ""),
                "message":       d.get("message", ""),
                "message_en":    d.get("message_en", ""),
                "debut":         d.get("debut"),
                "expected_resumption": d.get("expected_resumption", "Indefinite duration"),
                "resumption_time":    d.get("resumption_time"),
                "directions":    [_DIRECTION_LABEL.get(dr, dr) for dr in d.get("directions", [])],
                "affected_stops": d.get("affected_stops", []),
                "nb_stops":     len(d.get("affected_stops", [])),
            }
            perturbations.append(entry)

        # All minor notices across all lines
        all_minors = []
        for key, info in METRO_LINES.items():
            for m in self._status().get(key, {}).get("minor_notices", []):
                all_minors.append({
                    "ligne":      info["name"],
                    "route_id":   info["route_id"],
                    "message":    m.get("message", m) if isinstance(m, dict) else m,
                    "message_en": m.get("message_en", "") if isinstance(m, dict) else "",
                    "debut":      m.get("debut") if isinstance(m, dict) else None,
                    "fin":        m.get("fin")   if isinstance(m, dict) else None,
                    "arrets":     m.get("arrets", []) if isinstance(m, dict) else [],
                })

        return {
            "disruptions":        perturbations,
            "nb_disruptions":     len(perturbations),
            "minor_notices":         all_minors,
            "nb_minor_notices":      len(all_minors),
            "last_api_update":     data.get("api_timestamp"),
            "last_update":          data.get("last_update", ""),
        }

    def _status(self) -> dict:
        return self.coordinator.data.get("metro_status", {}) if self.coordinator.data else {}

    def _disrupted(self) -> list[tuple]:
        return [
            (k, d) for k, d in self._status().items()
            if d.get("status") not in (STATUS_NORMAL, STATUS_UNKNOWN)
        ]

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success


# ─────────────────────────────────────────────────────────────────────────────
# Stop departures sensor
# ─────────────────────────────────────────────────────────────────────────────

class STMDepartureSensor(CoordinatorEntity[STMStopCoordinator], SensorEntity):
    _attr_icon                       = "mdi:bus-clock"
    _attr_native_unit_of_measurement = "min"

    def __init__(self, coordinator, entry, stop_id, route_id, max_dep, gtfs) -> None:
        super().__init__(coordinator)
        self._stop_id  = stop_id
        self._route_id = route_id
        self._max_dep  = max_dep
        self._gtfs     = gtfs
        self._attr_unique_id = f"stm_stop_{stop_id}_{route_id}"
        route_label = f" ligne {route_id}" if route_id else ""
        self._attr_name = f"STM Stop {stop_id}{route_label}"

    @property
    def name(self) -> str:
        if self._gtfs and self._gtfs.stop_exists(self._stop_id):
            stop_name  = self._gtfs.stop_name(self._stop_id)
            route_part = f" ligne {self._route_id}" if self._route_id else ""
            return f"STM {stop_name}{route_part}"
        return self._attr_name

    @property
    def native_value(self) -> int | None:
        deps = self._deps()
        return deps[0]["minutes"] if deps else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        deps = self._deps()
        data = self.coordinator.data or {}

        # ── Format each departure ─────────────────────────────────────────────
        formatted = []
        for d in deps:
            headsign   = self._gtfs.trip_headsign(d["trip_id"])  if self._gtfs else ""
            route_name = self._gtfs.route_name(d["route_id"])    if self._gtfs else d["route_id"]
            dep_local  = d["departure_time"].astimezone(_MTL_TZ)

            delay_min  = round(d["delay_sec"] / 60) if d.get("delay_sec") else 0
            delay_str  = (
                f"+{delay_min} min late" if delay_min > 0
                else f"{abs(delay_min)} min early" if delay_min < 0
                else "On time"
            )

            dep_entry: dict[str, Any] = {
                "route":          d["route_id"],
                "route_name":     route_name,
                "direction":      headsign,
                "minutes":        d["minutes"],
                "scheduled_time": dep_local.strftime("%H:%M"),
                "punctuality":    delay_str,
                "delay_sec":     d.get("delay_sec", 0),
            }

            # Only add status if not normal
            if d.get("is_cancelled"):
                dep_entry["status"] = "Cancelled"
            elif d.get("is_skipped"):
                dep_entry["status"] = "Stop skipped"
            elif d.get("is_added"):
                dep_entry["status"] = "Added service"
            else:
                dep_entry["status"] = "Scheduled"

            formatted.append(dep_entry)

        # ── Stop info from GTFS ───────────────────────────────────────────────
        stop_name  = f"Stop {self._stop_id}"
        stop_lat   = stop_lon = None
        stop_valid = False

        if self._gtfs:
            stop_name  = self._gtfs.stop_name(self._stop_id)
            coords     = self._gtfs.stop_coords(self._stop_id)
            stop_valid = self._gtfs.stop_exists(self._stop_id)
            if coords:
                stop_lat, stop_lon = coords

        # ── Vehicles currently on the route at this stop ──────────────────────
        nearby_vehicles = []
        if self._route_id and data.get("vehicles_by_route"):
            nearby_vehicles = [
                {
                    "label":        v["label"],
                    "latitude":     v["latitude"],
                    "longitude":    v["longitude"],
                    "speed_kmh":  v["speed_kmh"],
                    "bearing":          v["bearing"],
                    "status":       v["current_status"],
                    "occupancy":   v["occupancy"],
                    "current_stop": v["current_stop_id"],
                }
                for v in data["vehicles_by_route"].get(self._route_id, [])
            ]

        # ── Summary string ────────────────────────────────────────────────────
        next_summary = "No departures"
        if formatted:
            f0    = formatted[0]
            dest  = f" → {f0['direction']}" if f0["direction"] else ""
            delay = f" ({f0['ponctualite']})" if f0["delay_sec"] != 0 else ""
            next_summary = f"Line {f0['route']}{dest} in {f0['minutes']} min ({f0['scheduled_time']}){delay}"

        attrs: dict[str, Any] = {
            # Departures list
            ATTR_DEPARTURES:    formatted,
            "nb_departures":       len(formatted),
            "next_departure":  next_summary,
            # Stop info
            "stop_id":          self._stop_id,
            "stop_name":        stop_name,
            "stop_valid":      stop_valid,
            "route_filter":     self._route_id or "all",
            # Metadata
            "last_update":      data.get("last_update"),
        }

        if stop_lat:
            attrs["stop_latitude"]  = stop_lat
            attrs["stop_longitude"] = stop_lon

        if nearby_vehicles:
            attrs["vehicles_on_route"] = nearby_vehicles
            attrs["nb_vehicles"]       = len(nearby_vehicles)

        return attrs

    def _deps(self) -> list:
        if not self.coordinator.data:
            return []
        deps = self.coordinator.data.get("stop_departures", {}).get(self._stop_id, [])
        if self._route_id:
            deps = [d for d in deps if d["route_id"] == self._route_id]
        return deps[: self._max_dep]

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success
