# ⚡ Purgearr

[🇫🇷 Français](#-purgearr--français) · [🇬🇧 English](#-purgearr--english)

---

## ⚡ Purgearr — Français

> **🚧 Version bêta — En développement actif**  
> Développé pour un usage personnel sur NAS Raspberry Pi. Fonctionnel au quotidien, mais certaines fonctionnalités sont encore en cours de stabilisation.

> **⛔ Mode automatique désactivé**  
> Le mode de suppression automatique (webhook Jellyfin → queue → suppression différée) **ne doit pas être utilisé pour l'instant**. Utilisez uniquement la suppression manuelle depuis l'interface.

**Purgearr** est une interface web de gestion de bibliothèque pour les setups **Jellyfin + Radarr + Sonarr + Transmission**. Il donne une vue complète de ton catalogue, identifie les contenus jamais regardés, les torrents morts qui occupent de l'espace, et permet de supprimer proprement via Radarr/Sonarr + Jellyfin.

### Aperçu

| 👁️ Page Regardés | 🚫 Suggestions — Jamais regardés |
|---|---|
| ![Watched](https://i.ibb.co/0y7d7PzZ/Capture-d-cran-2026-07-24-163340.png) | ![Never watched](https://i.ibb.co/h1Y1vT7w/Capture-d-cran-2026-07-24-163441.png) |

| 💀 Torrents morts (ratio 0) | 🗑️ Historique des suppressions |
|---|---|
| ![Dead seed](https://i.ibb.co/DfB94tz7/Capture-d-cran-2026-07-24-163523.png) | ![History](https://i.ibb.co/t98YjsP/Capture-d-cran-2026-07-24-163616.png) |

| 📋 Journal événementiel | ✅ Modal de confirmation |
|---|---|
| ![Event log](https://i.ibb.co/7NWmHB7m/Capture-d-cran-2026-07-24-163649.png) | ![Confirm deletion](https://i.ibb.co/WCCbMbD/Capture-d-cran-2026-07-24-163748.png) |

### Fonctionnalités

- 📊 **Dashboard** — stats globales, queue de suppression, historique récent
- 👁️ **Regardés** — liste des contenus vus, progression par utilisateur, statut "prêt à supprimer"
- 🧹 **Suggestions** — jamais regardés / vus partiellement / torrents morts (ratio 0) + stats seeding Transmission en temps réel
- 📚 **Mon Catalogue** — vue complète du catalogue Jellyfin, Films & Séries séparés, paginée (60/page), avec :
  - Recherche par titre
  - Tri : date d'ajout, alphabétique, poids
  - Filtres : jamais vu, partiel, tous vus, en seed actif, seedé inactif, ratio mort, sans torrent
  - Poids total Films / Séries affiché (agrégé depuis Transmission, dédupliqué multi-tracker)
  - Suppression directe depuis la carte
- 🛡️ **Protection** — whitelist films/séries à ne jamais supprimer (recherche live Jellyfin), favoris Jellyfin automatiquement protégés
- 🗑️ **Historique** — toutes les suppressions + scanner les copies résiduelles
- 📋 **Logs** — journal événementiel filtrable
- ⚙️ **Paramètres** — configuration complète depuis l'interface web
- 🌐 **Multi-langue** — Français / English
- 🧲 **Multi-tracker** — détecte tous les torrents qui seedent le même fichier sur plusieurs trackers (dédupliqué pour le calcul de taille)
- 📋 **Détection copies/hardlinks** — scan inode puis SHA-256 avant suppression
- 👥 **Multi-utilisateurs** — définir des utilisateurs requis (tous doivent avoir regardé)
- 🔍 **Modal de confirmation** — avant chaque suppression, affiche exactement ce qui sera effacé

### Prérequis

- Python 3.10+
- Radarr, Sonarr, Transmission et Jellyfin accessibles en réseau local

### Installation

**1. Cloner le dépôt**

```bash
git clone https://github.com/Lekarov/Purgearr.git
cd Purgearr
```

**2. Environnement virtuel + dépendances**

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**3. Premier démarrage**

```bash
python main.py
```

Interface accessible sur `http://[IP]:7979`. Configure tous les services depuis **⚙️ Paramètres**.

**4. Dossier `data/` — ne jamais supprimer**

```
data/
├── config.json        ← configuration (URLs, clés API, règles)
├── protected.json     ← whitelist des contenus protégés
├── purgearr.db        ← historique, queue, événements
└── cache/             ← cache temporaire (régénéré automatiquement)
```

> Ce dossier est exclu de git — tes données sont toujours préservées lors des mises à jour.

### Service systemd (Raspberry Pi)

```ini
[Unit]
Description=Purgearr Media Manager
After=network.target

[Service]
Type=simple
WorkingDirectory=/chemin/vers/Purgearr
ExecStart=/chemin/vers/Purgearr/venv/bin/python main.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable purgearr
sudo systemctl start purgearr
```

### Webhook Jellyfin (optionnel — ne pas activer le mode auto)

Le webhook reçoit les événements `PlaybackStop` de Jellyfin en temps réel. Installe le plugin **Webhook** depuis le catalogue Jellyfin :

- **URL** : `http://[IP]:7979/webhook/jellyfin`
- **Événement** : `Playback Stop`

> ⛔ Même avec le webhook actif, **ne pas activer le mode Auto** dans les paramètres — cette fonctionnalité est en cours de stabilisation.

### Mise à jour

```bash
git pull
# Redémarrer le service
sudo systemctl restart purgearr
```

### Stack technique

| Composant | Technologie |
|---|---|
| Backend | FastAPI + Uvicorn |
| Base de données | SQLite via SQLAlchemy |
| Scheduler | APScheduler |
| Templates | Jinja2 |
| Frontend | HTML/CSS/JS vanilla |

### Licence

MIT — utilise et adapte librement.

---

## ⚡ Purgearr — English

> **🚧 Beta — Active development**  
> Built for personal use on a Raspberry Pi NAS. Fully functional for daily use, but some features are still being stabilized.

> **⛔ Auto mode is disabled**  
> The automatic deletion mode (Jellyfin webhook → queue → deferred deletion) **must not be used at this time**. Use manual deletion from the interface only.

**Purgearr** is a web-based library management interface for **Jellyfin + Radarr + Sonarr + Transmission** setups. It gives you a complete view of your catalogue, identifies never-watched content, dead torrents wasting space, and lets you cleanly delete via Radarr/Sonarr + Jellyfin.

### Screenshots

| 👁️ Watched | 🚫 Suggestions — Never watched |
|---|---|
| ![Watched](https://i.ibb.co/0y7d7PzZ/Capture-d-cran-2026-07-24-163340.png) | ![Never watched](https://i.ibb.co/h1Y1vT7w/Capture-d-cran-2026-07-24-163441.png) |

| 💀 Dead torrents (ratio 0) | 🗑️ Deletion history |
|---|---|
| ![Dead seed](https://i.ibb.co/DfB94tz7/Capture-d-cran-2026-07-24-163523.png) | ![History](https://i.ibb.co/t98YjsP/Capture-d-cran-2026-07-24-163616.png) |

| 📋 Event log | ✅ Confirm deletion modal |
|---|---|
| ![Event log](https://i.ibb.co/7NWmHB7m/Capture-d-cran-2026-07-24-163649.png) | ![Confirm deletion](https://i.ibb.co/WCCbMbD/Capture-d-cran-2026-07-24-163748.png) |

### Features

- 📊 **Dashboard** — global stats, deletion queue, recent history
- 👁️ **Watched** — viewed content list, per-user progress, "ready to delete" status
- 🧹 **Suggestions** — never watched / partially watched / dead torrents (ratio 0) + live Transmission seeding stats
- 📚 **My Catalogue** — full Jellyfin catalogue view, Films & Series separated, paginated (60/page), with:
  - Title search
  - Sort by: date added, alphabetical, file size
  - Filter by: never watched, partial, all watched, actively seeding, idle seed, dead ratio, no torrent
  - Total size for Films / Series (aggregated from Transmission, multi-tracker deduplicated)
  - Direct deletion from the card
- 🛡️ **Protection** — whitelist of movies/shows to never delete (live Jellyfin search), Jellyfin favorites automatically protected
- 🗑️ **History** — all past deletions + leftover copy scanner
- 📋 **Logs** — filterable event journal
- ⚙️ **Settings** — full configuration from the web UI
- 🌐 **Multi-language** — French / English
- 🧲 **Multi-tracker** — detects all torrents seeding the same file across multiple trackers (deduplicated for size calculation)
- 📋 **Copy/hardlink detection** — inode then SHA-256 scan before deletion
- 👥 **Multi-user** — define required users (all must have watched before deletion is suggested)
- 🔍 **Confirmation modal** — before each deletion, shows exactly what will be removed

### Requirements

- Python 3.10+
- Radarr, Sonarr, Transmission and Jellyfin accessible on the local network

### Installation

**1. Clone the repository**

```bash
git clone https://github.com/Lekarov/Purgearr.git
cd Purgearr
```

**2. Virtual environment + dependencies**

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**3. First launch**

```bash
python main.py
```

Interface available at `http://[IP]:7979`. Configure all services from the **⚙️ Settings** page.

**4. `data/` folder — never delete**

```
data/
├── config.json        ← configuration (URLs, API keys, rules)
├── protected.json     ← protected content whitelist
├── purgearr.db        ← history, queue, watch events
└── cache/             ← temporary cache (auto-regenerated)
```

> This folder is excluded from git — your data is always preserved on updates.

### systemd Service (Raspberry Pi)

```ini
[Unit]
Description=Purgearr Media Manager
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/Purgearr
ExecStart=/path/to/Purgearr/venv/bin/python main.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable purgearr
sudo systemctl start purgearr
```

### Jellyfin Webhook (optional — do not enable auto mode)

The webhook receives `PlaybackStop` events from Jellyfin in real time. Install the **Webhook** plugin from the Jellyfin catalog:

- **URL**: `http://[IP]:7979/webhook/jellyfin`
- **Event**: `Playback Stop`

> ⛔ Even with the webhook active, **do not enable Auto mode** in settings — this feature is still being stabilized.

### Updating

```bash
git pull
# Restart the service
sudo systemctl restart purgearr
```

### Tech Stack

| Component | Technology |
|---|---|
| Backend | FastAPI + Uvicorn |
| Database | SQLite via SQLAlchemy |
| Scheduler | APScheduler |
| Templates | Jinja2 |
| Frontend | Vanilla HTML/CSS/JS |

### License

MIT — use and adapt freely.
