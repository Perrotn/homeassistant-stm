"""
GTFSStaticManager — downloads and caches the STM static GTFS data.

Loaded once at HA startup, refreshed weekly.
Provides:
  - stop name + coordinates by stop_id
  - route short name by route_id
  - trip headsign by trip_id
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
import os
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import aiohttp

_LOGGER = logging.getLogger(__name__)

GTFS_URL       = "http://www.stm.info/sites/default/files/gtfs/gtfs_stm.zip"
CACHE_FILENAME = "stm_gtfs_static.zip"
REFRESH_DAYS   = 7


class GTFSStaticManager:
    """Manages the STM static GTFS dataset."""

    def __init__(self, storage_dir: str) -> None:
        self._storage_dir  = Path(storage_dir)
        self._cache_path   = self._storage_dir / CACHE_FILENAME
        self._loaded       = False
        self._last_refresh: Optional[datetime] = None

        # Lookup tables
        self.stops:  dict[str, dict] = {}   # stop_id  → {name, lat, lon}
        self.routes: dict[str, str]  = {}   # route_id → short_name
        self.trips:  dict[str, str]  = {}   # trip_id  → headsign

    # ── Public API ────────────────────────────────────────────────────────────

    async def async_ensure_loaded(self) -> None:
        """Download if needed, parse, populate lookup tables."""
        if self._loaded and not self._needs_refresh():
            return
        try:
            await self._async_download_and_parse()
        except Exception as err:
            _LOGGER.warning("GTFS static load failed: %s", err)
            # Try cached version
            if self._cache_path.exists() and not self._loaded:
                try:
                    await asyncio.get_event_loop().run_in_executor(
                        None, self._parse_zip, self._cache_path.read_bytes()
                    )
                    self._loaded = True
                    _LOGGER.info("STM GTFS loaded from cache")
                except Exception as err2:
                    _LOGGER.error("GTFS cache parse failed: %s", err2)

    def stop_name(self, stop_id: str) -> str:
        return self.stops.get(stop_id, {}).get("name", f"Stop {stop_id}")

    def stop_coords(self, stop_id: str) -> tuple[float, float] | None:
        s = self.stops.get(stop_id)
        if s:
            return s.get("lat"), s.get("lon")
        return None

    def route_name(self, route_id: str) -> str:
        return self.routes.get(route_id, route_id)

    def trip_headsign(self, trip_id: str) -> str:
        return self.trips.get(trip_id, "")

    def stop_exists(self, stop_id: str) -> bool:
        return stop_id in self.stops

    # ── Internal ──────────────────────────────────────────────────────────────

    def _needs_refresh(self) -> bool:
        if self._last_refresh is None:
            return True
        age = datetime.now(tz=timezone.utc) - self._last_refresh
        return age > timedelta(days=REFRESH_DAYS)

    async def _async_download_and_parse(self) -> None:
        _LOGGER.info("Downloading STM GTFS static data...")
        async with aiohttp.ClientSession() as session:
            async with session.get(
                GTFS_URL,
                timeout=aiohttp.ClientTimeout(total=60),
                headers={"User-Agent": "HomeAssistant-STM/2.0"},
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status} downloading GTFS")
                data = await resp.read()

        # Save cache
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_bytes(data)

        await asyncio.get_event_loop().run_in_executor(None, self._parse_zip, data)
        self._loaded       = True
        self._last_refresh = datetime.now(tz=timezone.utc)
        _LOGGER.info(
            "STM GTFS loaded: %d stops, %d routes, %d trips",
            len(self.stops), len(self.routes), len(self.trips),
        )

    def _parse_zip(self, data: bytes) -> None:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self._parse_stops(z)
            self._parse_routes(z)
            self._parse_trips(z)

    def _parse_stops(self, z: zipfile.ZipFile) -> None:
        self.stops = {}
        with z.open("stops.txt") as f:
            reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
            for row in reader:
                sid = row.get("stop_id", "").strip()
                if not sid:
                    continue
                self.stops[sid] = {
                    "name": row.get("stop_name", f"Stop {sid}").strip(),
                    "lat":  float(row.get("stop_lat", 0) or 0),
                    "lon":  float(row.get("stop_lon", 0) or 0),
                }

    def _parse_routes(self, z: zipfile.ZipFile) -> None:
        self.routes = {}
        with z.open("routes.txt") as f:
            reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
            for row in reader:
                rid  = row.get("route_id", "").strip()
                name = row.get("route_short_name", "").strip() or row.get("route_long_name", "").strip()
                if rid:
                    self.routes[rid] = name or rid

    def _parse_trips(self, z: zipfile.ZipFile) -> None:
        self.trips = {}
        with z.open("trips.txt") as f:
            reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
            for row in reader:
                tid      = row.get("trip_id", "").strip()
                headsign = row.get("trip_headsign", "").strip()
                if tid and headsign:
                    self.trips[tid] = headsign
