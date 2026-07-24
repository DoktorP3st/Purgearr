# ⚡ Purgearr

[🇫🇷 Français](#-purgearr--français) · [🇬🇧 English](#-purgearr--english)

---

## ⚡ Purgearr — Français

> **⚠️ Projet en développement actif**  
> Développé pour un usage personnel sur NAS Raspberry Pi. Publié tel quel — fonctionnel, mais l'installation nécessite quelques manipulations manuelles.

**Purgearr** est une interface web de gestion automatique des médias pour les setups **Jellyfin + Radarr + Sonarr + Transmission**. Il surveille ce que tu regardes et supprime automatiquement (ou sur demande) les contenus vus pour libérer de l'espace disque.

### Fonctionnalités

- 📊 **Dashboard** — stats globales, queue de suppression, historique récent
- 👁️ **Regardés** — liste des contenus vus, progression par utilisateur, statut "prêt à supprimer"
- 🧹 **Suggestions** — jamais regardés / vus partiellement + stats de seeding Transmission en temps réel
- 🛡️ **Protection** — whitelist films/séries à ne jamais supprimer (recherche live Jellyfin)
- 🗑️ **Historique** — toutes les suppressions + scanner les copies résiduelles après coup
- ⚙️ **Paramètres** — configuration complète depuis l'interface web, sans éditer de fichiers
- 🔗 **Liens services** — Radarr, Sonarr, Transmission et Jellyfin cliquables dans la sidebar et le modal de confirmation
- 🔍 **Modal de confirmation** — avant chaque suppression, affiche exactement ce qui sera effacé, les copies détectées et les trackers impliqués
- 🧲 **Multi-tracker** — détecte et stoppe tous les torrents qui seedent le même fichier (multi-tracker support)
- 📋 **Détection copies/hardlinks** — scan par inode puis SHA-256 dans les chemins additionnels avant suppression
- 🤖 **Mode Auto / Manuel** — suppression automatique selon des règles ou sur demande
- 👥 **Multi-utilisateurs** — définir des utilisateurs requis (tous doivent avoir regardé)
- 🪝 **Webhook Jellyfin** — réception des événements PlaybackStop en temps réel

### Prérequis

- Python 3.10+
- Radarr, Sonarr, Transmission et Jellyfin accessibles en réseau local
- Plugin **Webhook** installé dans Jellyfin (optionnel mais recommandé)

### Installation

**1. Cloner le dépôt**

```bash
git clone https://github.com/Lekarov/Purgearr.git
cd Purgearr
```

**2. Créer l'environnement virtuel et installer les dépendances**

```bash
python3 -m venv venv
source venv/bin/activate
pip install fastapi uvicorn sqlalchemy apscheduler jinja2 requests python-multipart
```

**3. Premier démarrage**

```bash
python main.py
```

L'interface est accessible sur `http://[IP]:7979`. Configure tous les services depuis **Paramètres**.

**4. Dossier `data/` — ne jamais supprimer**

```
data/
├── config.json          ← configuration complète (URLs, clés, règles)
├── protected.json       ← whitelist films/séries protégés
├── purgearr.db          ← historique, queue, événements de visionnage
└── cleanup_index.json   ← index des suppressions pour scan des restes
```

### Webhook Jellyfin (optionnel)

Installe le plugin **Webhook** depuis le catalogue Jellyfin, puis configure :
- **URL** : `http://[IP_DU_SERVEUR]:7979/webhook/jellyfin`
- **Événement** : `Playback Stop`

Sans webhook, le scan d'import manuel reste disponible dans la sidebar.

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

### Mise à jour

```bash
git pull
# Redémarrer l'application
```

Le dossier `data/` n'est jamais touché par git — tes données sont préservées.

### Stack technique

| Composant | Technologie |
|---|---|
| Backend | FastAPI + Uvicorn |
| Base de données | SQLite WAL via SQLAlchemy |
| Scheduler | APScheduler |
| Templates | Jinja2 |
| Frontend | HTML/CSS/JS vanilla |

### Licence

MIT — utilise et adapte librement.

---

## ⚡ Purgearr — English

> **⚠️ Active development**  
> Built for personal use on a Raspberry Pi NAS. Published as-is — it works, but setup requires a few manual steps.

**Purgearr** is a web interface for automatic media management in **Jellyfin + Radarr + Sonarr + Transmission** setups. It watches what you watch and automatically (or on demand) deletes viewed content to free up disk space.

### Features

- 📊 **Dashboard** — global stats, deletion queue, recent history
- 👁️ **Watched** — list of viewed content, per-user progress, "ready to delete" status
- 🧹 **Suggestions** — never watched / partially watched + live Transmission seeding stats
- 🛡️ **Protection** — whitelist of movies/shows to never delete (live Jellyfin search)
- 🗑️ **History** — all past deletions + scan for leftover copies after the fact
- ⚙️ **Settings** — full configuration from the web UI, no file editing required
- 🔗 **Service links** — Radarr, Sonarr, Transmission and Jellyfin clickable in sidebar and confirmation modal
- 🔍 **Confirmation modal** — before each deletion, shows exactly what will be removed, detected copies, and involved trackers
- 🧲 **Multi-tracker** — detects and stops all torrents seeding the same file across multiple trackers
- 📋 **Copy/hardlink detection** — inode then SHA-256 scan across additional paths before deletion
- 🤖 **Auto / Manual mode** — rule-based automatic deletion or manual on-demand
- 👥 **Multi-user** — define required users (all must have watched before deletion)
- 🪝 **Jellyfin webhook** — real-time PlaybackStop events

### Requirements

- Python 3.10+
- Radarr, Sonarr, Transmission and Jellyfin accessible on the local network
- **Webhook** plugin installed in Jellyfin (optional but recommended)

### Installation

**1. Clone the repository**

```bash
git clone https://github.com/Lekarov/Purgearr.git
cd Purgearr
```

**2. Create virtual environment and install dependencies**

```bash
python3 -m venv venv
source venv/bin/activate
pip install fastapi uvicorn sqlalchemy apscheduler jinja2 requests python-multipart
```

**3. First launch**

```bash
python main.py
```

The interface is available at `http://[IP]:7979`. Configure all services from the **Settings** page.

**4. `data/` folder — never delete**

```
data/
├── config.json          ← full configuration (URLs, API keys, rules)
├── protected.json       ← protected movies/shows whitelist
├── purgearr.db          ← history, queue, watch events
└── cleanup_index.json   ← deletion index for leftover scanning
```

### Jellyfin Webhook (optional)

Install the **Webhook** plugin from the Jellyfin catalog, then configure:
- **URL**: `http://[SERVER_IP]:7979/webhook/jellyfin`
- **Event**: `Playback Stop`

Without the webhook, a manual import scan is available in the sidebar.

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

### Updating

```bash
git pull
# Restart the application
```

The `data/` folder is never touched by git — your data is preserved.

### Tech Stack

| Component | Technology |
|---|---|
| Backend | FastAPI + Uvicorn |
| Database | SQLite WAL via SQLAlchemy |
| Scheduler | APScheduler |
| Templates | Jinja2 |
| Frontend | Vanilla HTML/CSS/JS |

### License

MIT — use and adapt freely.
