# STM – Société de transport de Montréal

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

Intégration Home Assistant pour la **Société de transport de Montréal (STM)**.

## Fonctionnalités

- 🚇 **État du service métro** en temps réel pour les 4 lignes (Verte, Orange, Jaune, Bleue)
- 🔔 **Binary sensors** de perturbation par ligne (parfait pour les automatisations)
- 🚌 **Prochains départs** à n'importe quel arrêt STM
- 📍 **Noms d'arrêts et destinations** enrichis via les données GTFS statiques
- ⏰ **Heures locales** Montréal (America/Toronto)
- 🔄 **Coordinateur partagé** — un seul appel API pour tous les arrêts configurés

## Prérequis

- Un compte sur le [portail développeurs STM](https://portail.developpeurs.stm.info/apihub)
- Une clé API STM (gratuite) avec les APIs suivantes activées :
  - `Données Ouverte iBUS - GTFS-Realtime (v2.0)`
  - `Données Ouverte iBUS - État du Service (v2)`

## Installation via HACS

1. Dans HACS → **Intégrations** → ⋮ → **Dépôts personnalisés**
2. Ajouter l'URL de ce dépôt, catégorie : **Intégration**
3. Installer **STM**
4. Redémarrer Home Assistant

## Configuration

1. **Paramètres** → **Appareils et services** → **Ajouter une intégration** → **STM**
2. Entrer votre **clé API**
3. Choisir le type :
   - 🚇 **État du métro** → crée 9 entités (4 sensors de statut + 4 binary sensors + 1 alerte globale)
   - 🚌 **Arrêt de bus/métro** → entrer le numéro d'arrêt (et optionnellement la ligne)

Répéter l'ajout pour chaque arrêt supplémentaire.

## Entités créées

### Entrée « État du métro »
| Entité | Type | Description |
|--------|------|-------------|
| `sensor.stm_metro_ligne_verte` | Sensor | `Normal` / `Perturbé` / `Interrompu` |
| `sensor.stm_metro_ligne_orange` | Sensor | idem |
| `sensor.stm_metro_ligne_jaune` | Sensor | idem |
| `sensor.stm_metro_ligne_bleue` | Sensor | idem |
| `sensor.stm_alertes_metro` | Sensor | Nombre de lignes perturbées |
| `binary_sensor.stm_perturbation_ligne_verte` | Binary | `on` = problème détecté |
| `binary_sensor.stm_perturbation_ligne_orange` | Binary | idem |
| `binary_sensor.stm_perturbation_ligne_jaune` | Binary | idem |
| `binary_sensor.stm_perturbation_ligne_bleue` | Binary | idem |

### Entrée « Arrêt »
| Entité | Type | Description |
|--------|------|-------------|
| `sensor.stm_arret_XXXXX` | Sensor | Minutes avant le prochain départ |

Attributs : liste des prochains départs avec ligne, direction, heure.

## Exemples d'automatisations

### Notification si le métro Orange est perturbé
```yaml
automation:
  - alias: "Alerte métro Orange"
    trigger:
      - platform: state
        entity_id: binary_sensor.stm_perturbation_ligne_orange
        to: "on"
    action:
      - service: notify.mobile_app
        data:
          title: "⚠️ Métro Orange perturbé"
          message: "{{ state_attr('sensor.stm_metro_ligne_orange', 'message') }}"
```

### Rappel avant de partir prendre le bus
```yaml
automation:
  - alias: "Rappel bus 94"
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

## Ressources

- [Portail développeurs STM](https://portail.developpeurs.stm.info/apihub)
- [Trouver un numéro d'arrêt](https://www.stm.info/fr/infos/reseaux/bus)
