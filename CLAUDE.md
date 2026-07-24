# Purgearr — Contexte projet pour Claude

## Description
Application de gestion et suppression automatique de médias pour NAS Raspberry Pi.
Stack : FastAPI + Jinja2 + SQLite (SQLAlchemy) + APScheduler
Port : 7979
Chemin Pi : `/srv/mergerfs/DATA_POOL/HDD 1TO/appdata/Purgearr/`
Virtualenv : `venv/bin/activate`
Lancement : `source venv/bin/activate && python main.py`

## Services connectés
- **Radarr** — suppression films + ajout liste exclusion
- **Sonarr** — suppression séries/épisodes
- **Transmission** — arrêt seeding + suppression torrent
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
    ├── settings.html     — paramètres + chemins bibliothèque
    ├── history.html      — historique + bouton "Scanner les restes"
    └── dashboard.html    — stats + queue
```

## Données persistantes (dossier `data/`)

| Fichier | Contenu | Écrit par |
|---|---|---|
| `data/config.json` | Config : URLs, clés API, règles, extra_paths, library roots, scheduler | `save_config()` |
| `data/protected.json` | IDs Jellyfin + titres protégés | `save_protected()` |
| `data/purgearr.db` | WatchEvent, DeletionQueue, DeletionHistory | sync + pipeline + scheduler |
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
  "scheduler": { "scan_interval_minutes": 360, "queue_interval_minutes": 360 }
}
```

## Correspondance chemins Jellyfin ↔ filesystem réel

Jellyfin monte les bibliothèques sous `/media/film/` et `/media/série/` mais les fichiers réels sont sous :
- Films : `/srv/mergerfs/DATA_POOL/HDD 1TO/Torrent/downloads/film/`
- Séries : `/srv/mergerfs/DATA_POOL/HDD 1TO/Torrent/downloads/série/`

`resolve_real_path(jf_path, item_type)` dans `config.py` résout ce décalage en testant toutes les racines connues (library_root_movies → library_root_series → extra_paths) et en strippant progressivement les composants du chemin Jellyfin.

## Flux suppression (2 étapes avec confirmation)

1. Clic "Supprimer" → `deleteWithScan()` (JS, base.html)
2. `POST /api/scan/copies` → `scan_copies_smart()` → résultats dans modal
3. Confirmation → `POST /api/delete/manual`
4. Pipeline : hash calculé → Transmission stop → **cleanup_index saved** → Radarr/Sonarr → run_cleanup() → Jellyfin refresh
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

## JS partagé (base.html)

- `deleteWithScan(id, type, title, seriesTitle, cardElemId)` — flux suppression
- `confirmDelete()` — exécute la suppression après confirmation
- `showToast(msg, type)` — toast bottom-right
- `showCleanupReport(label, c)` — modal rapport nettoyage
- `sidebarScan()` — scan d'import Jellyfin depuis sidebar

## Problèmes déjà résolus

- **Boutons onclick cassés** : `tojson` produit des guillemets. Fix : data attributes + `addEventListener`.
- **SQLite database locked** : WAL mode + timeout 30s.
- **Jellyfin get_item sans user context** : utiliser `/Users/{admin}/Items/{id}`.
- **Faux positifs titre** : supprimé — matching hash-only uniquement.
- **Hash perdu après suppression Radarr** : `source_hash` calculé AVANT, stocké dans cleanup_index.
- **Chemin Jellyfin ≠ disque** : `resolve_real_path()` essaie toutes les racines connues.
- **Release folder = toute la bibliothèque** : fix → parent du fichier trouvé, pas premier composant depuis base.
- **library_root écrasé à vide** : `routes.py` garde la valeur existante si le form envoie vide.
- **Séries : path Jellyfin = dossier** : `resolve_real_path` accepte `os.path.isdir`, `scan_copies_smart` trouve premier fichier vidéo dans le dossier.

## Webhook Jellyfin

Plugin **Webhook** → `http://192.168.1.38:7979/webhook/jellyfin`  
Événement : `PlaybackStop`

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
