"""Constants for the STM integration."""

DOMAIN = "stm"

# ── Config entry fields ───────────────────────────────────────────────────────
CONF_API_KEY        = "api_key"
CONF_ENTRY_TYPE     = "entry_type"
CONF_STOP_ID        = "stop_id"
CONF_ROUTE_ID       = "route_id"
CONF_MAX_DEPARTURES = "max_departures"

# ── Entry types ───────────────────────────────────────────────────────────────
ENTRY_TYPE_METRO = "metro"
ENTRY_TYPE_STOP  = "stop"

# ── Timing ───────────────────────────────────────────────────────────────────
DEFAULT_SCAN_INTERVAL      = 30          # seconds — GTFS-RT polling
METRO_SCAN_INTERVAL        = 60          # seconds — État du service
GTFS_REFRESH_INTERVAL_DAYS = 7           # days — static GTFS refresh

DEFAULT_MAX_DEPARTURES = 3

# ── GTFS Static ───────────────────────────────────────────────────────────────
GTFS_STATIC_URL = "http://www.stm.info/sites/default/files/gtfs/gtfs_stm.zip"

# ── GTFS-RT (protobuf) ────────────────────────────────────────────────────────
STM_GTFS_API_HEADER = "apiKey"
STM_API_BASE        = "https://api.stm.info/pub/od/gtfs-rt/ic/v2"
STM_API_TRIPS       = f"{STM_API_BASE}/tripUpdates"
STM_API_VEHICLES    = f"{STM_API_BASE}/vehiclePositions"

# ── État du Service (JSON) ────────────────────────────────────────────────────
STM_SERVICE_API_HEADER = "apikey"
STM_API_SERVICE_STATUS = "https://api.stm.info/pub/od/i3/v2/messages/etatservice"
STM_API_ORIGIN_HEADER  = "https://www.stm.info"

# ── Timezone ──────────────────────────────────────────────────────────────────
STM_TIMEZONE = "America/Toronto"

# ── Metro lines ───────────────────────────────────────────────────────────────
METRO_LINES = {
    "verte":  {"name": "Ligne Verte",  "color": "#008C00", "route_id": "1"},
    "orange": {"name": "Ligne Orange", "color": "#F6821F", "route_id": "2"},
    "jaune":  {"name": "Ligne Jaune",  "color": "#FFD700", "route_id": "4"},
    "bleue":  {"name": "Ligne Bleue",  "color": "#0083CA", "route_id": "5"},
}

# ── Service status ────────────────────────────────────────────────────────────
STATUS_NORMAL     = "Normal"
STATUS_PERTURBE   = "Perturbé"
STATUS_INTERROMPU = "Interrompu"
STATUS_UNKNOWN    = "Inconnu"

# ── Keywords for alert classification ────────────────────────────────────────
SERVICE_DISRUPTION_KW = [
    "service interrompu", "interrompue", "interruption", "suspension",
    "suspendu", "perturbé", "ralentissement", "délai", "retard",
    "arrêt de service", "hors service",
]
MINOR_NOTICE_KW = [
    "accès", "entrée", "édicule", "ascenseur", "escalier mécanique",
    "fermé pour travaux", "fermé jusqu", "relocalisé", "déplacé",
]

# ── Attributes ────────────────────────────────────────────────────────────────
ATTR_DEPARTURES  = "departures"
ATTR_ALERTS      = "alerts"
ATTR_STOP_NAME   = "stop_name"
ATTR_ROUTE_NAME  = "route_name"
