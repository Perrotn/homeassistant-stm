"""
STM coordinators — optimized for minimal resource usage.

Key optimizations:
- STMStopCoordinator: single HTTP session, early filtering, pre-compiled sets
- STMMetroCoordinator: minimal parsing, no redundant dict copies
- Both: no intermediate lists, slots where possible, lazy string formatting
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import aiohttp
from google.transit import gtfs_realtime_pb2

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    DEFAULT_SCAN_INTERVAL,
    METRO_SCAN_INTERVAL,
    STM_GTFS_API_HEADER,
    STM_SERVICE_API_HEADER,
    STM_API_TRIPS,
    STM_API_VEHICLES,
    STM_API_SERVICE_STATUS,
    STM_API_ORIGIN_HEADER,
    STM_TIMEZONE,
    METRO_LINES,
    STATUS_NORMAL,
    STATUS_PERTURBE,
    STATUS_INTERROMPU,
    STATUS_UNKNOWN,
    SERVICE_DISRUPTION_KW,
    MINOR_NOTICE_KW,
)

_LOGGER = logging.getLogger(__name__)
_MTL_TZ = ZoneInfo(STM_TIMEZONE)

# Pre-built lookup tables (computed once at import time)
_ROUTE_TO_LINE: dict[str, str] = {
    info["route_id"]: key for key, info in METRO_LINES.items()
}

_SCHED_REL = {0: "Scheduled", 1: "Added", 2: "Cancelled", 3: "Unscheduled", 5: "Duplicated"}
_STOP_REL  = {0: "Scheduled", 1: "Skipped", 2: "No data"}
_VEH_STATUS = {0: "Approaching", 1: "Stopped", 2: "In transit"}
_OCCUPANCY  = {
    0: "Empty", 1: "Many seats available", 2: "Few seats available",
    3: "Standing only", 4: "Crushed standing", 5: "Full", 6: "No data",
}

# Pre-compiled keyword sets for alert classification
_DISRUPTION_KW = frozenset(SERVICE_DISRUPTION_KW)
_MINOR_KW      = frozenset(MINOR_NOTICE_KW)


def _classify_alert(text: str) -> str:
    t = text.lower()
    if "service normal" in t:
        return "normal"
    if any(kw in t for kw in _DISRUPTION_KW):
        return "disruption"
    if any(kw in t for kw in _MINOR_KW):
        return "minor"
    return "normal"


def _fmt_ts(ts: int | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=_MTL_TZ).strftime("%Y-%m-%d %H:%M")


def _resumption_label(end_ts: int | None) -> str:
    if not end_ts:
        return "Indefinite duration"
    end_dt = datetime.fromtimestamp(end_ts, tz=_MTL_TZ)
    delta  = end_dt - datetime.now(tz=_MTL_TZ)
    secs   = delta.total_seconds()
    if secs < 0:
        return "Resumption in progress"
    mins = int(secs / 60)
    t    = end_dt.strftime("%H:%M")
    if mins < 60:
        return f"Expected resumption in ~{mins} min ({t})"
    if delta.days == 0:
        return f"Expected resumption at {t}"
    if delta.days == 1:
        return f"Expected resumption tomorrow at {t}"
    return f"Expected resumption on {end_dt.strftime('%d %b at %H:%M')}"


# ── Shared stop coordinator ───────────────────────────────────────────────────

class STMStopCoordinator(DataUpdateCoordinator):
    """
    Downloads GTFS-RT once per interval for all stop entries.
    Single aiohttp session, minimal allocations, early-exit loops.
    """

    def __init__(self, hass: HomeAssistant, api_key: str) -> None:
        super().__init__(
            hass, _LOGGER, name=f"{DOMAIN}_stops",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        # Reuse headers dict — created once
        self._headers = {STM_GTFS_API_HEADER: api_key}
        # Track which stop IDs are actually watched to skip irrelevant data
        self.watched_stops: set[str] = set()

    async def _async_update_data(self) -> dict:
        now_utc = datetime.now(tz=timezone.utc)
        now_ts  = now_utc.timestamp()

        try:
            # Single session for both requests
            async with aiohttp.ClientSession() as session:
                trips_data, vehicles_data = await _fetch_both(
                    session, self._headers
                )
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Network error GTFS-RT: {err}") from err

        # ── Parse trip updates ────────────────────────────────────────────────
        trips_feed = gtfs_realtime_pb2.FeedMessage()
        trips_feed.ParseFromString(trips_data)

        stop_deps: dict[str, list] = {}
        watched   = self.watched_stops  # local ref avoids repeated attr lookup

        for entity in trips_feed.entity:
            if not entity.HasField("trip_update"):
                continue
            tu   = entity.trip_update
            trip = tu.trip
            rid  = trip.route_id
            tid  = trip.trip_id
            srel = _SCHED_REL.get(trip.schedule_relationship, "Scheduled")

            for stu in tu.stop_time_update:
                sid = stu.stop_id
                # Skip stops nobody is watching (saves dict allocs)
                if watched and sid not in watched:
                    continue

                dep_ts = dep_delay = arr_ts = arr_delay = None
                if stu.HasField("departure") and stu.departure.time:
                    dep_ts    = stu.departure.time
                    dep_delay = stu.departure.delay
                if stu.HasField("arrival") and stu.arrival.time:
                    arr_ts    = stu.arrival.time
                    arr_delay = stu.arrival.delay

                ref_ts = dep_ts or arr_ts
                if not ref_ts or ref_ts <= now_ts:
                    continue

                stop_rel = _STOP_REL.get(stu.schedule_relationship, "Scheduled")
                minutes  = int((ref_ts - now_ts) / 60)
                dep_utc  = datetime.fromtimestamp(ref_ts, tz=timezone.utc)

                entry = {
                    "route_id":        rid,
                    "trip_id":         tid,
                    "departure_time":  dep_utc,
                    "minutes":         minutes,
                    "delay_sec":       dep_delay or arr_delay or 0,
                    "stop_sequence":   stu.stop_sequence,
                    "schedule_relationship": srel,
                    "stop_relationship":     stop_rel,
                    "is_skipped":    stop_rel == "Skipped",
                    "is_added":      srel == "Added",
                    "is_cancelled":  srel == "Cancelled",
                }
                if sid in stop_deps:
                    stop_deps[sid].append(entry)
                else:
                    stop_deps[sid] = [entry]

        # Sort each stop's departures in-place
        for deps in stop_deps.values():
            deps.sort(key=lambda x: x["departure_time"])

        # ── Parse vehicle positions ───────────────────────────────────────────
        vehicles_feed = gtfs_realtime_pb2.FeedMessage()
        vehicles_feed.ParseFromString(vehicles_data)

        vehicles_by_route: dict[str, list] = {}

        for entity in vehicles_feed.entity:
            if not entity.HasField("vehicle"):
                continue
            v   = entity.vehicle
            rid = v.trip.route_id
            if not rid:
                continue

            occ_raw = v.occupancy_status if v.HasField("occupancy_status") else None

            veh = {
                "id":              entity.id,
                "label":           v.vehicle.label,
                "route_id":        rid,
                "trip_id":         v.trip.trip_id,
                "latitude":        round(v.position.latitude,  6),
                "longitude":       round(v.position.longitude, 6),
                "bearing":         round(v.position.bearing, 1) if v.position.bearing else None,
                "speed_kmh":       round(v.position.speed * 3.6, 1) if v.position.speed else 0,
                "current_stop_id": v.stop_id,
                "current_status":  _VEH_STATUS.get(v.current_status, "In transit"),
                "occupancy":       _OCCUPANCY.get(occ_raw, "No data") if occ_raw is not None else None,
            }

            if rid in vehicles_by_route:
                vehicles_by_route[rid].append(veh)
            else:
                vehicles_by_route[rid] = [veh]

        return {
            "stop_departures":   stop_deps,
            "vehicles_by_route": vehicles_by_route,
            "last_update":       now_utc.astimezone(_MTL_TZ).isoformat(),
        }


async def _fetch_both(
    session: aiohttp.ClientSession, headers: dict
) -> tuple[bytes, bytes]:
    """Fetch trips and vehicles concurrently."""
    import asyncio
    trips_task    = asyncio.ensure_future(_fetch_raw(session, STM_API_TRIPS,    headers))
    vehicles_task = asyncio.ensure_future(_fetch_raw(session, STM_API_VEHICLES, headers))
    trips_data, vehicles_data = await asyncio.gather(trips_task, vehicles_task)
    return trips_data, vehicles_data


async def _fetch_raw(
    session: aiohttp.ClientSession, url: str, headers: dict
) -> bytes:
    async with session.get(url, headers=headers,
                           timeout=aiohttp.ClientTimeout(total=20)) as resp:
        if resp.status == 401:
            raise UpdateFailed("Invalid API key (GTFS-RT 401)")
        if resp.status == 429:
            raise UpdateFailed("Rate limit exceeded (429)")
        if resp.status != 200:
            raise UpdateFailed(f"HTTP {resp.status} (GTFS-RT)")
        return await resp.read()


# ── Metro coordinator ─────────────────────────────────────────────────────────

class STMMetroCoordinator(DataUpdateCoordinator):
    """Fetches État du Service — minimal allocations, no redundant copies."""

    def __init__(self, hass: HomeAssistant, api_key: str) -> None:
        super().__init__(
            hass, _LOGGER, name=f"{DOMAIN}_metro",
            update_interval=timedelta(seconds=METRO_SCAN_INTERVAL),
        )
        self._headers = {
            STM_SERVICE_API_HEADER: api_key,
            "Origin": STM_API_ORIGIN_HEADER,
        }

    async def _async_update_data(self) -> dict:
        now = datetime.now(tz=timezone.utc)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    STM_API_SERVICE_STATUS,
                    headers=self._headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 401:
                        raise UpdateFailed("Invalid API key (Service Status)")
                    if resp.status != 200:
                        raise UpdateFailed(f"HTTP {resp.status} (Service Status)")
                    raw = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Network error service status: {err}") from err

        return {
            "metro_status":  _parse_metro(raw),
            "api_timestamp": _fmt_ts(raw.get("header", {}).get("timestamp")),
            "last_update":   now.astimezone(_MTL_TZ).isoformat(),
        }


def _parse_metro(raw: dict) -> dict:
    """Parse metro status — single pass over alerts, minimal allocations."""
    # Initialize buckets for all lines
    buckets: dict[str, dict[str, list]] = {
        k: {"normal": [], "disruption": [], "minor": []}
        for k in METRO_LINES
    }

    for alert in raw.get("alerts", ()):
        ap       = alert.get("active_periods", {})
        start_ts = ap.get("start")
        end_ts   = ap.get("end")

        entities = alert.get("informed_entities", ())
        route_ids = {e["route_short_name"] for e in entities if "route_short_name" in e}

        # Get French description first, fall back to any language
        desc_fr = ""
        desc_en = ""
        for t in alert.get("description_texts", ()):
            lang = t.get("language", "")
            txt  = t.get("text", "")
            if not txt:
                continue
            if lang == "fr" and not desc_fr:
                desc_fr = txt
            elif lang == "en" and not desc_en:
                desc_en = txt
            if desc_fr and desc_en:
                break

        if not desc_fr:
            desc_fr = desc_en
        if not desc_fr:
            continue

        cat   = _classify_alert(desc_fr)
        stops = [e["stop_code"] for e in entities if "stop_code" in e]
        dirs_ = list({e["direction_id"] for e in entities if "direction_id" in e})

        entry = {
            "desc_fr":  desc_fr,
            "desc_en":  desc_en,
            "start_ts": start_ts,
            "end_ts":   end_ts,
            "debut":    _fmt_ts(start_ts),
            "fin":      _fmt_ts(end_ts),
            "stops":    stops,
            "dirs":     dirs_,
        }

        for rid in route_ids:
            lk = _ROUTE_TO_LINE.get(rid)
            if lk:
                buckets[lk][cat].append(entry)

    # Build result
    result: dict[str, dict] = {}
    for lk, cats in buckets.items():
        dis  = cats["disruption"]
        min_ = cats["minor"]
        nor  = cats["normal"]

        minors = [_fmt_minor(e) for e in min_]

        if dis:
            main   = min(dis, key=lambda x: x["start_ts"] or 0)
            end_ts = next((e["end_ts"] for e in dis if e["end_ts"]), None)
            interrupted = any(
                kw in e["desc_fr"].lower() for e in dis
                for kw in ("interrompue", "interruption", "suspension", "suspendu")
            )
            result[lk] = {
                "status":               STATUS_INTERROMPU if interrupted else STATUS_PERTURBE,
                "message":              main["desc_fr"],
                "message_en":           main["desc_en"],
                "disruptions":          [_fmt_disruption(e) for e in dis],
                "minor_notices":        minors,
                "affected_stops":       list({s for e in dis for s in e["stops"]}),
                "affected_directions":  list({d for e in dis for d in e["dirs"]}),
                "disruption_start":     main["debut"],
                "expected_resumption":  _resumption_label(end_ts),
                "resumption_time":      _fmt_ts(end_ts),
            }
        elif nor or min_:
            result[lk] = {
                "status":               STATUS_NORMAL,
                "message":              "Normal service",
                "message_en":           "Normal service",
                "disruptions":          [],
                "minor_notices":        minors,
                "affected_stops":       [],
                "affected_directions":  [],
                "disruption_start":     None,
                "expected_resumption":  None,
                "resumption_time":      None,
            }
        else:
            result[lk] = {
                "status":               STATUS_UNKNOWN,
                "message":              "No data available",
                "message_en":           "No data available",
                "disruptions":          [],
                "minor_notices":        [],
                "affected_stops":       [],
                "affected_directions":  [],
                "disruption_start":     None,
                "expected_resumption":  None,
                "resumption_time":      None,
            }

    return result


def _fmt_disruption(e: dict) -> dict:
    return {
        "message":    e["desc_fr"],
        "message_en": e["desc_en"],
        "start":      e["debut"],
        "end":        e["fin"],
        "directions": e["dirs"],
        "stops":      e["stops"],
    }


def _fmt_minor(e: dict) -> dict:
    return {
        "message":    e["desc_fr"],
        "message_en": e["desc_en"],
        "start":      e["debut"],
        "end":        e["fin"],
        "stops":      e["stops"],
    }
