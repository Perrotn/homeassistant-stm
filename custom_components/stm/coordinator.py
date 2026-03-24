"""STM coordinators — extracts every available field from both APIs."""
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

# GTFS-RT schedule_relationship codes
_SCHED_REL = {0: "Prévu", 1: "Ajouté", 2: "Annulé", 3: "Non prévu", 5: "Dupliqué"}
_STOP_REL  = {0: "Prévu", 1: "Sauté", 2: "Sans données"}

# Vehicle current_status codes
_VEH_STATUS = {
    0: "En approche",
    1: "À l'arrêt",
    2: "En transit",
}

# Occupancy status codes
_OCCUPANCY = {
    0: "Vide",
    1: "Beaucoup de places",
    2: "Peu de places",
    3: "Debout seulement",
    4: "Bondé debout",
    5: "Plein",
    6: "Données non disponibles",
}


def _classify_alert(text: str) -> str:
    t = text.lower()
    if "service normal" in t:
        return "normal"
    if any(kw in t for kw in SERVICE_DISRUPTION_KW):
        return "disruption"
    if any(kw in t for kw in MINOR_NOTICE_KW):
        return "minor"
    return "normal"


def _fmt_ts(ts: int | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=_MTL_TZ).strftime("%Y-%m-%d %H:%M")


def _resumption_label(end_ts: int | None) -> str:
    if not end_ts:
        return "Durée indéterminée"
    end_dt = datetime.fromtimestamp(end_ts, tz=_MTL_TZ)
    now    = datetime.now(tz=_MTL_TZ)
    delta  = end_dt - now
    if delta.total_seconds() < 0:
        return "Reprise en cours"
    total_min = int(delta.total_seconds() / 60)
    if total_min < 60:
        return f"Reprise prévue dans ~{total_min} min ({end_dt.strftime('%H:%M')})"
    if delta.days == 0:
        return f"Reprise prévue à {end_dt.strftime('%H:%M')}"
    if delta.days == 1:
        return f"Reprise prévue demain à {end_dt.strftime('%H:%M')}"
    return f"Reprise prévue le {end_dt.strftime('%d %b à %H:%M')}"


# ── Shared stop coordinator ───────────────────────────────────────────────────

class STMStopCoordinator(DataUpdateCoordinator):
    """Downloads GTFS-RT once, serves all stop sensors."""

    def __init__(self, hass: HomeAssistant, api_key: str) -> None:
        super().__init__(
            hass, _LOGGER, name=f"{DOMAIN}_stops",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self._headers = {STM_GTFS_API_HEADER: api_key}

    async def _async_update_data(self) -> dict:
        now = datetime.now(tz=timezone.utc)
        try:
            async with aiohttp.ClientSession() as session:
                trips_feed    = await self._fetch_pb(session, STM_API_TRIPS)
                vehicles_feed = await self._fetch_pb(session, STM_API_VEHICLES)
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Erreur réseau GTFS-RT: {err}") from err

        # ── Trip updates → departures per stop ───────────────────────────────
        stop_deps: dict[str, list] = {}

        for entity in trips_feed.entity:
            if not entity.HasField("trip_update"):
                continue
            tu   = entity.trip_update
            trip = tu.trip

            # Trip-level schedule relationship
            sched_rel = _SCHED_REL.get(trip.schedule_relationship, "Prévu")

            for stu in tu.stop_time_update:
                sid = stu.stop_id
                if not sid:
                    continue

                # Prefer departure, fallback to arrival
                dep_ts  = dep_delay = None
                arr_ts  = arr_delay = None

                if stu.HasField("departure"):
                    dep_ts    = stu.departure.time or None
                    dep_delay = stu.departure.delay  # seconds

                if stu.HasField("arrival"):
                    arr_ts    = stu.arrival.time or None
                    arr_delay = stu.arrival.delay

                ref_ts = dep_ts or arr_ts
                if not ref_ts:
                    continue

                dep_utc = datetime.fromtimestamp(ref_ts, tz=timezone.utc)
                if dep_utc <= now:
                    continue

                minutes = int((dep_utc - now).total_seconds() / 60)

                # Stop-level relationship (SKIPPED = arrêt sauté)
                stop_rel = _STOP_REL.get(stu.schedule_relationship, "Prévu")

                stop_deps.setdefault(sid, []).append({
                    # Core
                    "route_id":       trip.route_id,
                    "trip_id":        trip.trip_id,
                    "departure_time": dep_utc,
                    "minutes":        minutes,
                    # Timing detail
                    "departure_ts":   dep_ts,
                    "arrival_ts":     arr_ts,
                    "delay_sec":      dep_delay or arr_delay or 0,
                    "stop_sequence":  stu.stop_sequence,
                    # Status
                    "schedule_relationship": sched_rel,
                    "stop_relationship":     stop_rel,
                    "is_skipped":     stop_rel == "Sauté",
                    "is_added":       sched_rel == "Ajouté",
                    "is_cancelled":   sched_rel == "Annulé",
                })

        for sid in stop_deps:
            stop_deps[sid].sort(key=lambda x: x["departure_time"])

        # ── Vehicle positions ─────────────────────────────────────────────────
        vehicles: list[dict] = []

        for entity in vehicles_feed.entity:
            if not entity.HasField("vehicle"):
                continue
            v = entity.vehicle

            occupancy_raw = v.occupancy_status if v.HasField("occupancy_status") else None
            occupancy_str = _OCCUPANCY.get(occupancy_raw, "Données non disponibles") if occupancy_raw is not None else None

            vehicles.append({
                "id":              entity.id,
                "label":           v.vehicle.label,
                "route_id":        v.trip.route_id,
                "trip_id":         v.trip.trip_id,
                "start_time":      v.trip.start_time,
                "start_date":      v.trip.start_date,
                # Position
                "latitude":        round(v.position.latitude,  6),
                "longitude":       round(v.position.longitude, 6),
                "bearing":         round(v.position.bearing, 1) if v.position.bearing else None,
                "speed_kmh":       round(v.position.speed * 3.6, 1) if v.position.speed else 0,
                # Stop info
                "current_stop_id": v.stop_id,
                "current_status":  _VEH_STATUS.get(v.current_status, "En transit"),
                # Occupancy
                "occupancy":       occupancy_str,
                "timestamp":       _fmt_ts(v.timestamp) if v.timestamp else None,
            })

        return {
            "stop_departures": stop_deps,
            "vehicles":        vehicles,
            "vehicles_by_route": _index_vehicles_by_route(vehicles),
            "last_update":     now.astimezone(_MTL_TZ).isoformat(),
        }

    async def _fetch_pb(self, session, url):
        async with session.get(url, headers=self._headers,
                               timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status == 401:
                raise UpdateFailed("Clé API invalide (GTFS-RT 401)")
            if resp.status == 429:
                raise UpdateFailed("Limite de requêtes atteinte (429)")
            if resp.status != 200:
                raise UpdateFailed(f"HTTP {resp.status} (GTFS-RT)")
            data = await resp.read()
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(data)
        return feed


def _index_vehicles_by_route(vehicles: list[dict]) -> dict[str, list]:
    idx: dict[str, list] = {}
    for v in vehicles:
        rid = v.get("route_id", "")
        if rid:
            idx.setdefault(rid, []).append(v)
    return idx


# ── Metro coordinator ─────────────────────────────────────────────────────────

class STMMetroCoordinator(DataUpdateCoordinator):
    """Fetches État du Service — extracts every available field."""

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
                    STM_API_SERVICE_STATUS, headers=self._headers,
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status == 401:
                        raise UpdateFailed("Clé API invalide (État du Service)")
                    if resp.status != 200:
                        raise UpdateFailed(f"HTTP {resp.status} (État du Service)")
                    raw = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Erreur réseau état du service: {err}") from err

        return {
            "metro_status":  self._parse(raw),
            "api_timestamp": _fmt_ts(raw.get("header", {}).get("timestamp")),
            "last_update":   now.astimezone(_MTL_TZ).isoformat(),
        }

    def _parse(self, raw: dict) -> dict:
        ROUTE_TO_LINE = {info["route_id"]: key for key, info in METRO_LINES.items()}

        result = {
            key: {
                "status":         STATUS_UNKNOWN,
                "message":        "Données non disponibles",
                "message_en":     "",
                "avis_mineurs":   [],
                "perturbations":  [],
                "arrets_touches": [],
                "directions":     [],
                "debut":          None,
                "reprise_prevue": None,
                "reprise_ts":     None,
            }
            for key in METRO_LINES
        }

        buckets: dict[str, dict[str, list]] = {
            k: {"normal": [], "disruption": [], "minor": []}
            for k in METRO_LINES
        }

        for alert in raw.get("alerts", []):
            # ── Timing ───────────────────────────────────────────────────────
            ap       = alert.get("active_periods", {})
            start_ts = ap.get("start")
            end_ts   = ap.get("end")

            # ── Texts (FR + EN) ───────────────────────────────────────────────
            desc_fr = next(
                (t["text"] for t in alert.get("description_texts", [])
                 if t.get("language") == "fr" and t.get("text")), ""
            )
            desc_en = next(
                (t["text"] for t in alert.get("description_texts", [])
                 if t.get("language") == "en" and t.get("text")), ""
            )
            if not desc_fr:
                desc_fr = desc_en  # fallback

            header_fr = next(
                (t["text"] for t in alert.get("header_texts", [])
                 if t.get("language") == "fr" and t.get("text")), ""
            )

            if not desc_fr:
                continue

            # ── Informed entities ─────────────────────────────────────────────
            entities      = alert.get("informed_entities", [])
            route_ids     = {e["route_short_name"] for e in entities if "route_short_name" in e}
            stop_codes    = [e["stop_code"]        for e in entities if "stop_code"        in e]
            directions    = list({e["direction_id"] for e in entities if "direction_id" in e})

            cat   = _classify_alert(desc_fr)
            entry = {
                "desc_fr":    desc_fr,
                "desc_en":    desc_en,
                "header":     header_fr,
                "start_ts":   start_ts,
                "end_ts":     end_ts,
                "debut":      _fmt_ts(start_ts),
                "fin":        _fmt_ts(end_ts),
                "stops":      stop_codes,
                "directions": directions,
            }

            for rid in route_ids:
                lk = ROUTE_TO_LINE.get(rid)
                if lk:
                    buckets[lk][cat].append(entry)

        # ── Build result per line ─────────────────────────────────────────────
        for lk, cats in buckets.items():
            dis  = cats["disruption"]
            min_ = cats["minor"]
            nor  = cats["normal"]

            # Collect all affected stops and directions across all disruption alerts
            all_stops      = list({s for e in dis for s in e.get("stops", [])})
            all_directions = list({d for e in dis for d in e.get("directions", [])})

            if dis:
                main    = sorted(dis, key=lambda x: x["start_ts"] or 0)[0]
                end_ts  = next((e["end_ts"] for e in dis if e["end_ts"]), None)
                interrupted = any(
                    kw in e["desc_fr"].lower() for e in dis
                    for kw in ["interrompue", "interruption", "suspension", "suspendu"]
                )
                result[lk] = {
                    "status":         STATUS_INTERROMPU if interrupted else STATUS_PERTURBE,
                    "message":        main["desc_fr"],
                    "message_en":     main["desc_en"],
                    "perturbations":  [
                        {
                            "message":    e["desc_fr"],
                            "message_en": e["desc_en"],
                            "debut":      e["debut"],
                            "fin":        e["fin"],
                            "directions": e["directions"],
                            "arrets":     e["stops"],
                        }
                        for e in dis
                    ],
                    "avis_mineurs": [
                        {
                            "message":    e["desc_fr"],
                            "message_en": e["desc_en"],
                            "debut":      e["debut"],
                            "fin":        e["fin"],
                            "arrets":     e["stops"],
                        }
                        for e in min_
                    ],
                    "arrets_touches": all_stops,
                    "directions":     all_directions,
                    "debut":          main["debut"],
                    "reprise_prevue": _resumption_label(end_ts),
                    "reprise_ts":     _fmt_ts(end_ts),
                }
            elif nor:
                result[lk] = {
                    "status":         STATUS_NORMAL,
                    "message":        "Service normal",
                    "message_en":     "Normal service",
                    "perturbations":  [],
                    "avis_mineurs":   [
                        {
                            "message":    e["desc_fr"],
                            "message_en": e["desc_en"],
                            "debut":      e["debut"],
                            "fin":        e["fin"],
                            "arrets":     e["stops"],
                        }
                        for e in min_
                    ],
                    "arrets_touches": [],
                    "directions":     [],
                    "debut":          None,
                    "reprise_prevue": None,
                    "reprise_ts":     None,
                }
            elif min_:
                result[lk] = {
                    "status":         STATUS_NORMAL,
                    "message":        "Service normal",
                    "message_en":     "Normal service",
                    "perturbations":  [],
                    "avis_mineurs":   [
                        {
                            "message":    e["desc_fr"],
                            "message_en": e["desc_en"],
                            "debut":      e["debut"],
                            "fin":        e["fin"],
                            "arrets":     e["stops"],
                        }
                        for e in min_
                    ],
                    "arrets_touches": [],
                    "directions":     [],
                    "debut":          None,
                    "reprise_prevue": None,
                    "reprise_ts":     None,
                }
            else:
                result[lk] = {
                    "status":         STATUS_UNKNOWN,
                    "message":        "Aucune donnée",
                    "message_en":     "",
                    "perturbations":  [],
                    "avis_mineurs":   [],
                    "arrets_touches": [],
                    "directions":     [],
                    "debut":          None,
                    "reprise_prevue": None,
                    "reprise_ts":     None,
                }

        return result
