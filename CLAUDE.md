# Purgearr — Contexte projet pour Claude

## Description
Application web de gestion et suppression automatique de médias pour NAS Raspberry Pi.
Stack : FastAPI + Jinja2 + SQLite (SQLAlchemy) + APScheduler
Port : 7979
Chemin Pi : `/srv/mergerfs/DATA_POOL/HDD 1TO/appdata/Purgearr/`
Virtualenv : `venv/bin/activate`
Lancement : `source venv/bin/activate && python main.py`

**Règle de déploiement :** l'utilisateur modifie les fichiers locaux (Windows) et les replace manuellement sur le Pi. Ne jamais demander à l'utilisateur de taper des commandes SSH pour éditer du code.

## Services connectés
- **Radarr** — suppression films + ajout liste exclusion
- **Sonarr** — suppression séries/épisodes
- **Transmission** — arrêt seeding + suppression torrent (multi-tracker)
- **Jellyfin** — webhook PlaybackStop, récupération historique, recherche

## Architecture fichiers

```
/
├── main.py               — point d'entrée FastAPI (port 7979)
├── config.py             — lecture/écriture config JSON + helpers chemins
├── database.py           — SQLAlchemy + WAL mode
├── scheduler.py          — APScheduler (scan + queue)
│
├── data/                 ← NE PAS ÉCRASER lors des mises à jour
│   ├── config.json       — toute la config (URLs, clés, règles, chemins)
│   ├── protected.json    — whitelist items protégés
│   ├── purgearr.db       — historique, queue, watch events (SQLite WAL)
│   └── cleanup_index.json — index des suppressions (hash + métadonnées)
│
├── core/
│   ├── pipeline.py       — delete_movie(), delete_episode()
│   ├── fileops.py        — scan_copies_smart(), run_cleanup(), hash_file()
│   ├── cleanup_store.py  — gestion data/cleanup_index.json
│   ├── sync.py           — sync_watch_data() depuis Jellyfin
│   ├── eventlog.py       — journal événementiel (info/warning/error, 10 catégories)
│   └── rules.py          — logique readiness multi-users
│
├── services/
│   ├── radarr.py / sonarr.py / transmission.py / jellyfin.py
│   └── factory.py        — get_radarr(), get_jellyfin(), etc.
│
├── api/
│   ├── routes.py         — toutes les routes web + API JSON
│   └── webhook.py        — POST /webhook/jellyfin (PlaybackStop)
│
└── templates/
    ├── base.html         — layout + sidebar + modals partagés + JS commun
    ├── watched.html      — contenus regardés, filtres, suppression manuelle
    ├── suggestions.html  — jamais vus / vus partiellement + badges seeding
    ├── protected.html    — whitelist avec recherche live Jellyfin
    ├── settings.html     — paramètres + chemins bibliothèque + config journal
    ├── history.html      — historique + bouton "Scanner les restes"
    ├── logs.html         — journal événementiel (filtres, timeline, stats, auto-refresh)
    ├── transmission.html — page orphelins Transmission (accessible via /transmission)
    └── dashboard.html    — stats + queue
```

## Données persistantes (dossier `data/`)

| Fichier | Contenu | Écrit par |
|---|---|---|
| `data/config.json` | Config : URLs, clés API, règles, extra_paths, library roots, scheduler | `save_config()` |
| `data/protected.json` | IDs Jellyfin + titres protégés | `save_protected()` |
| `data/purgearr.db` | WatchEvent, DeletionQueue, DeletionHistory, **LogEntry** (event_logs) | sync + pipeline + scheduler + eventlog |
| `data/cleanup_index.json` | Hash + métadonnées de chaque suppression | `core/cleanup_store.py` |

**Règle absolue :** ne JAMAIS écraser le dossier `data/` lors d'une mise à jour.

## Config (`data/config.json`) — clés importantes

```json
{
  "jellyfin":   { "url": "...", "api_key": "..." },
  "radarr":     { "url": "...", "api_key": "..." },
  "sonarr":     { "url": "...", "api_key": "..." },
  "transmission": { "host": "...", "port": 9091 },
  "library_root_movies": "/srv/mergerfs/DATA_POOL/HDD 1TO/Torrent/downloads/film/",
  "library_root_series": "/srv/mergerfs/DATA_POOL/HDD 1TO/Torrent/downloads/série/",
  "extra_paths": [
    "/srv/mergerfs/DATA_POOL/HDD 1TO/Torrent/downloads/complete/",
    "/srv/mergerfs/DATA_POOL/HDD 1TO/Torrent/downloads/Seeding/"
  ],
  "rules": { "mode": "manual", ... },
  "scheduler": { "scan_interval_minutes": 360, "queue_interval_minutes": 360 },
  "logs": { "enabled": true, "retention_days": 30, "max_entries": 10000 }
}
```

## Correspondance chemins Jellyfin ↔ filesystem réel

Jellyfin monte les bibliothèques sous `/media/film/` et `/media/série/` mais les fichiers réels sont sous :
- Films : `/srv/mergerfs/DATA_POOL/HDD 1TO/Torrent/downloads/film/`
- Séries : `/srv/mergerfs/DATA_POOL/HDD 1TO/Torrent/downloads/série/`

`resolve_real_path(jf_path, item_type)` dans `config.py` résout ce décalage en testant toutes les racines connues (library_root_movies → library_root_series → extra_paths) et en strippant progressivement les composants du chemin Jellyfin.

## Flux suppression (2 étapes avec confirmation)

1. Clic "Supprimer" → `deleteWithScan()` (JS, base.html)
2. `POST /api/scan/copies` → `scan_copies_smart()` → résultats + `service_links` dans modal
3. Confirmation → `POST /api/delete/manual`
4. Pipeline : hash calculé → Transmission stop ALL → **cleanup_index saved** → Radarr/Sonarr → run_cleanup() → Jellyfin refresh
5. Rapport nettoyage affiché

## Stratégie détection copies (`core/fileops.py`)

1. **Inode** : `(st_dev, st_ino)` identique = hardlink 100% fiable
2. **Hash SHA-256** : premiers 64 Ko pour fichiers cross-filesystem
   - `hash_file(path)` exporté pour calcul AVANT suppression Radarr/Sonarr
   - `source_hash` transmis JS → API → pipeline → cleanup
3. **Dossiers** (séries) : premier fichier vidéo dans le dossier sert de référence hash/inode
4. **Release folder** : = dossier parent du fichier trouvé (pas premier composant depuis base)

## Flux "Scanner les restes" (history.html)

1. Bouton "🔍 Scanner les restes" → `POST /api/cleanup/rescan`
2. Pour chaque entrée de `cleanup_index.json` avec un `source_hash` : `scan_copies_smart()` sur les chemins actuels
3. Résultats affichés par item : copies encore présentes, taille, chemin
4. Bouton par item "🗑️ Supprimer" → `POST /api/cleanup/delete-remains`
5. Ou "🗑️ Tout supprimer" → `POST /api/cleanup/purge-all`

## cleanup_index.json — structure d'une entrée

```json
{
  "id": "a3f9c1b2",
  "item_title": "Scream VI",
  "series_title": null,
  "item_type": "Movie",
  "jellyfin_item_id": "...",
  "source_hash": "f35d989595e7...",
  "file_path": "/srv/.../downloads/film/Scream VI (2023)/fichier.mkv",
  "file_size_bytes": 9645678912,
  "torrent_name": "Scream.6.2023.MULTi...",
  "scan_paths": ["/downloads/complete/", "/downloads/Seeding/", "/downloads/film/"],
  "deleted_at": "2026-07-24T02:23:11",
  "remains_checked_at": null,
  "remains_found": null
}
```

## Liens services dans le modal de confirmation

`POST /api/scan/copies` retourne `service_links` :

| Clé | Contenu |
|---|---|
| `jellyfin` | `{jellyfin_url}/web/index.html#!/details?id={item_id}` |
| `radarr` | `{radarr_url}/movie/{tmdbId}` — utilise **tmdbId** (pas l'id interne Radarr) |
| `sonarr` | `{sonarr_url}/series/{titleSlug}` |
| `transmission_torrents` | Liste `[{name, tracker_name, tracker_url}]` |

**Fallback Radarr/Sonarr** : si la recherche API échoue, lien vers `{url}/` (accueil du service).

### Extraction tracker Transmission (`_parse_tracker` + `_get_tracker_info`)

Chaque torrent peut avoir son tracker dans :
1. Le champ `comment` (texte libre contenant une URL, ex: `"Please keep seeding. https://tracker.cc/torrents/1234"`)
2. Le tableau `trackers[].announce` (fallback quand comment est vide)

`_parse_tracker(comment)` utilise une regex pour trouver la première URL `http/https/udp://` n'importe où dans le texte, extrait le domaine, et retourne `(tracker_name, tracker_url)` :
- URL d'announce (`/announce` dans le path) ou `udp://` → lien vers `https://{domain}/`
- URL directe (page torrent) → utilise l'URL complète

`_get_tracker_info(torrent)` essaie d'abord le `comment`, puis parcourt `trackers[]`.

Le champ `"trackers"` est inclus dans `TORRENT_FIELDS` dans `services/transmission.py`.

### Affichage dans le modal

- **Avec tracker URL** : `Transmission — {tracker_name} ↗` (cliquable), détail = nom du torrent
- **Sans tracker URL** : `Transmission — {nom_du_torrent}` (non cliquable)
- **Radarr/Jellyfin** : toujours cliquables si URL configurée (fallback = accueil service)
- **Sonarr** : cliquable uniquement pour les séries/épisodes

## Sidebar — Liens services cliquables

`GET /api/config/service-links` retourne les 4 URLs depuis le config :
```json
{
  "radarr": "http://...:7878",
  "sonarr": "http://...:8989",
  "transmission": "http://...:9091/",
  "jellyfin": "http://...:8096"
}
```

Dans `base.html`, les 4 noms (Radarr, Sonarr, Transmission, Jellyfin) sont des `<a>` invisibles (`color:inherit;text-decoration:none`) dont le `href` est défini en JS après fetch de cet endpoint. Le `cursor:pointer` s'active uniquement quand l'URL est configurée.

## JS partagé (base.html)

- `deleteWithScan(id, type, title, seriesTitle, cardElemId)` — flux suppression
- `confirmDelete()` — exécute la suppression après confirmation
- `showToast(msg, type)` — toast bottom-right
- `showCleanupReport(label, c)` — modal rapport nettoyage
- `sidebarScan()` — scan d'import Jellyfin depuis sidebar
- `_addConfirmRow(ul, path, detail, color, icon, href)` — ligne dans le modal de confirmation
  - Si `href` fourni : crée un `<a>` cliquable avec `cursor:pointer`, hover highlight, et indicateur `↗` visible
  - Si `href` null : ligne non cliquable

## Diagnostic dans le modal (panneau `🔍 Diagnostic`)

Toujours accessible (dans tous les états du scan). Affiche :
- Path Jellyfin brut vs path résolu (✓/✗)
- Hash calculé
- Chemins scannés
- Commentaires bruts des torrents Transmission trouvés
- Liens services construits + erreurs éventuelles

## Problèmes déjà résolus

- **Boutons onclick cassés** : data attributes + addEventListener
- **SQLite database locked** : WAL mode + timeout 30s
- **Jellyfin get_item sans user context** : utiliser `/Users/{admin}/Items/{id}`
- **Faux positifs titre** : supprimé — matching hash-only uniquement
- **Hash perdu après suppression Radarr** : `source_hash` calculé AVANT, stocké dans cleanup_index
- **Chemin Jellyfin ≠ disque** : `resolve_real_path()` essaie toutes les racines connues
- **Release folder = toute la bibliothèque** : fix → parent du fichier trouvé
- **library_root écrasé à vide** : `routes.py` garde la valeur existante si le form envoie vide
- **Séries : path Jellyfin = dossier** : `resolve_real_path` accepte `os.path.isdir`
- **Multi-tracker : 1 seul torrent stoppé** : `_stop_all_torrents` stoppe TOUS les torrents correspondants
- **Radarr lien vers "film introuvable"** : correction `movie['id']` → `movie['tmdbId']`
- **Tracker URL non extraite** : regex cherche URL n'importe où dans le texte du comment, pas juste au début
- **Tracker vide si comment absent** : fallback sur `trackers[].announce`
- **webhook.py crash si Jellyfin KO** : `item_details` initialisé à `{}` avant le try (NameError corrigé)
- **Corruption JSON sur crash** : `save_config`, `save_protected`, `save_index` utilisent tempfile + fsync + os.replace (écriture atomique)
- **JSONDecodeError au démarrage** : `load_config()` + `get_protected()` ont un try/except avec fallback `{}`
- **Fallback Radarr/Sonarr cassé** : bug de précédence `if/else/or` — remplacé par chaîne if explicite
- **process_queue DB corrompue** : `db.rollback()` ajouté sur exception avant de mettre le statut à "failed"
- **get_item sans user context** (process_queue + manual_delete) : récupère `admin_uid` et le passe à `jf.get_item()`
- **Sync bloque tous les users** : per-user try/except — un utilisateur KO ne stoppe plus les autres
- **Jobs APScheduler en double après veille** : `coalesce=True, max_instances=1, misfire_grace_time=300`
- **scan_copies_smart lent sur NAS** : filtre par taille avant hash SHA-256
- **settings page crash si clé logs absente** : `settings_page()` injecte les defaults via `setdefault` avant template

## Journal événementiel (`core/eventlog.py`)

API : `eventlog.info(category, message, **ctx)` / `warning()` / `error()`

10 catégories : `deletion` `watch` `queue` `protection` `sync` `scheduler` `webhook` `service` `config` `error`

- Stockage : table `event_logs` dans `data/purgearr.db`
- Auto-purge tous les 250 writes (rétention + max_entries)
- Configurable dans Paramètres (enabled, retention_days, max_entries)
- Page `/logs` avec filtres catégorie/niveau/recherche, auto-refresh 10s, stats, purge
- Ne lève jamais d'exception (silencieux sur erreur interne)

## Webhook Jellyfin

Plugin **Webhook** (catalogue officiel Jellyfin) → configurer une **Generic Destination** :
- **Webhook URL** : `http://192.168.1.38:7979/webhook/jellyfin`
- **Notification Type** : `Playback Stop` uniquement
- **Item Type** : Movies + Episodes
- **Send All Properties** : activé

Endpoint accepte uniquement POST — un GET depuis le navigateur renvoie 405 (normal).

## Service systemd (à créer)

```ini
[Unit]
Description=Purgearr

[Service]
WorkingDirectory=/srv/mergerfs/DATA_POOL/HDD 1TO/appdata/Purgearr
ExecStart=/srv/mergerfs/DATA_POOL/HDD 1TO/appdata/Purgearr/venv/bin/python main.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```
