# STM – Société de transport de Montréal

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2023.1%2B-blue.svg)](https://www.home-assistant.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Home Assistant integration for the **Société de transport de Montréal (STM)** — real-time metro service status and bus departures for Montréal, Québec.

---

> **⚠️ Disclaimer**
> This integration was developed with the assistance of [Claude](https://claude.ai), an AI assistant made by Anthropic. While it has been tested and is functional, it is provided as-is. Use at your own discretion. This project is not affiliated with, endorsed by, or officially supported by the STM or Anthropic.

---

## Features

- 🚇 **Real-time metro service status** for all 4 lines (Green, Orange, Yellow, Blue)
- 🔔 **Binary sensors** per metro line — perfect for automations (`on` = disruption detected)
- 🚌 **Next departures** at any STM stop with real stop names and trip destinations
- ⏱️ **Estimated resumption time** when a disruption has a known end time
- 🚦 **Punctuality info** — delay in minutes per departure
- 🚍 **Vehicle positions** — speed, bearing, occupancy, current stop
- 🗺️ **Stop coordinates** from GTFS static data
- ⏰ **Montreal local time** (America/Toronto timezone)
- 🔄 **Shared coordinator** — a single API call serves all configured stops

## Prerequisites

- A free account on the [STM Developer Portal](https://portail.developpeurs.stm.info/apihub)
- A free STM API key with the following APIs enabled:
  - `Données Ouverte iBUS - GTFS-Realtime (v2.0)`
  - `Données Ouverte iBUS - État du Service (v2)`

## Installation via HACS

1. In HACS → **Integrations** → ⋮ → **Custom repositories**
2. Add the URL of this repository, category: **Integration**
3. Install **STM**
4. Restart Home Assistant

## Manual Installation

1. Copy the `custom_components/stm/` folder into your `config/custom_components/` directory
2. Restart Home Assistant

## Configuration

1. **Settings** → **Devices & Services** → **Add Integration** → search for **STM**
2. Enter your **API key**
3. Choose the type:
   - 🚇 **Metro service status** → creates 9 entities (4 status sensors + 4 binary sensors + 1 alert summary)
   - 🚌 **Stop departures** → enter a stop number and optionally a route number

Repeat the integration setup for each additional stop you want to monitor.

## Entities

### Metro entry

| Entity | Type | State |
|--------|------|-------|
| `sensor.stm_metro_ligne_verte` | Sensor | `Normal` / `Perturbé` / `Interrompu` |
| `sensor.stm_metro_ligne_orange` | Sensor | same |
| `sensor.stm_metro_ligne_jaune` | Sensor | same |
| `sensor.stm_metro_ligne_bleue` | Sensor | same |
| `sensor.stm_alertes_metro` | Sensor | Number of disrupted lines |
| `binary_sensor.stm_perturbation_ligne_verte` | Binary | `on` = disruption |
| `binary_sensor.stm_perturbation_ligne_orange` | Binary | same |
| `binary_sensor.stm_perturbation_ligne_jaune` | Binary | same |
| `binary_sensor.stm_perturbation_ligne_bleue` | Binary | same |

#### Metro sensor attributes (when disrupted)

```yaml
message: "Service interrupted between Berri-UQAM and Snowdon"
message_en: "Service interrupted between..."
debut_perturbation: "2026-03-17 14:30"
reprise_prevue: "Resumption expected at 16:45"
reprise_ts: "2026-03-17 16:45"
directions_touchees: ["South", "North"]
arrets_touches: ["53045", "53046"]
perturbations:
  - message: "..."
    debut: "2026-03-17 14:30"
    fin: "2026-03-17 16:45"
    directions: ["S"]
    arrets: ["53045"]
avis_mineurs:
  - message: "Entrance B is closed for construction..."
    fin: "2026-04-30 00:00"
```

### Stop entry

| Entity | Type | State |
|--------|------|-------|
| `sensor.stm_STOP_NAME` | Sensor | Minutes until next departure |

#### Stop sensor attributes

```yaml
prochain_depart: "Line 94 → Pointe-aux-Trembles in 10 min (17:15) (+2 min late)"
departures:
  - route: "94"
    direction: "Pointe-aux-Trembles"
    minutes: 10
    scheduled_time: "17:15"
    ponctualite: "+2 min de retard"
    retard_sec: 120
    statut: "Prévu"       # or: Annulé / Arrêt sauté / Ajouté
stop_name: "De Lorimier / Ontario"
stop_latitude: 45.5234
stop_longitude: -73.5678
vehicules_en_route:
  - label: "40065"
    vitesse_kmh: 32.4
    statut: "En transit"
    occupation: "Peu de places"
```

## Automation Examples

### Notify when Orange line is disrupted

```yaml
automation:
  - alias: "Orange Line Alert"
    trigger:
      - platform: state
        entity_id: binary_sensor.stm_perturbation_ligne_orange
        to: "on"
    action:
      - service: notify.mobile_app
        data:
          title: "⚠️ Orange Line disrupted"
          message: >
            {{ state_attr('sensor.stm_metro_ligne_orange', 'message') }}
            {{ state_attr('sensor.stm_metro_ligne_orange', 'reprise_prevue') }}
```

### Reminder before catching the bus

```yaml
automation:
  - alias: "Bus 94 reminder"
    trigger:
      - platform: numeric_state
        entity_id: sensor.stm_arret_52934
        below: 8
    condition:
      - condition: time
        after: "07:00:00"
        before: "09:30:00"
        weekday: [mon, tue, wed, thu, fri]
    action:
      - service: notify.mobile_app
        data:
          message: >
            🚌 {{ state_attr('sensor.stm_arret_52934', 'prochain_depart') }}
```

### Lovelace card with logo

```yaml
type: entities
title: STM
header:
  type: picture
  image: /local/stm/logo.png
entities:
  - sensor.stm_metro_ligne_verte
  - sensor.stm_metro_ligne_orange
  - sensor.stm_metro_ligne_jaune
  - sensor.stm_metro_ligne_bleue
  - sensor.stm_alertes_metro
```

## API Data Sources

| Source | Data |
|--------|------|
| GTFS-RT `/tripUpdates` | Real-time departures, delays, cancellations |
| GTFS-RT `/vehiclePositions` | Bus positions, speed, bearing, occupancy |
| iBUS État du Service | Metro line status, disruption details, resumption times |
| GTFS Static (weekly) | Stop names, coordinates, route names, trip destinations |

## Resources

- [STM Developer Portal](https://portail.developpeurs.stm.info/apihub)
- [Find a stop number](https://www.stm.info/en/info/networks/bus)
- [STM Open Data](https://www.stm.info/en/about/developers)

## License

MIT License — see [LICENSE](LICENSE) for details.

## Disclaimer

This integration was developed with the assistance of [Claude](https://claude.ai) by Anthropic. It is an independent community project and is **not affiliated with, endorsed by, or officially supported by the STM (Société de transport de Montréal) or Anthropic**. Use of the STM API is subject to the [STM Terms of Use](https://www.stm.info/en/about/developers/terms-use).
