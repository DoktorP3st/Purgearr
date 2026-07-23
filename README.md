# ⚡ Purgearr

> **⚠️ Projet en développement actif**
> Développé pour un usage personnel sur NAS Raspberry Pi. Publié tel quel — il fonctionne, mais l'installation nécessite quelques manipulations manuelles et certaines fonctionnalités sont encore en cours de peaufinage. Issues et PRs bienvenus.

Purgearr est une interface web de gestion automatique des médias pour les setups **Jellyfin + Radarr + Sonarr + Transmission**. Il surveille ce que tu regardes et supprime automatiquement (ou sur demande) les contenus vus pour libérer de l'espace disque.

---

## Fonctionnalités

- **Dashboard** — stats globales, queue de suppression, historique
- **Regardés** — liste des contenus vus avec indicateur de progression par utilisateur
- **Suggestions** — jamais regardés / vus par certains seulement + stats de seeding Transmission (🔥/💤/💀)
- **Protection** — whitelist de films/séries à ne jamais supprimer (recherche live Jellyfin)
- **Nettoyage multi-chemins** — détecte les copies/hardlinks sur d'autres disques avant suppression (inode → SHA-256 → titre)
- **Modal de confirmation** — avant chaque suppression, affiche exactement ce qui va être effacé
- **Mode Auto / Manuel** — suppression automatique selon des règles configurables ou à la demande
- **Multi-utilisateurs** — définir des utilisateurs requis (tous doivent avoir regardé avant suppression)
- **Webhook Jellyfin** — réception des événements PlaybackStop en temps réel

---

## Prérequis

- Python 3.10+
- Radarr, Sonarr, Transmission et Jellyfin accessibles en réseau local
- Plugin **Webhook** installé dans Jellyfin (optionnel mais recommandé)

---

## Installation

### 1. Cloner le dépôt

```bash
git clone https://github.com/Lekarov/Purgearr.git
cd Purgearr
```

### 2. Installer les dépendances Python

```bash
pip install fastapi uvicorn sqlalchemy apscheduler jinja2 pyyaml requests python-multipart
```

### 3. Créer la configuration initiale

```bash
cp config.example.yaml config.yaml
```

Éditer `config.yaml` avec tes valeurs :

```yaml
radarr:
  url: "http://192.168.1.x:7878/"
  api_key: "ta_cle_api_radarr"

sonarr:
  url: "http://192.168.1.x:8989/"
  api_key: "ta_cle_api_sonarr"

transmission:
  host: "192.168.1.x"
  port: 9091
  username: ""   # laisser vide si pas d'authentification
  password: ""

jellyfin:
  url: "http://192.168.1.x:8096/"
  api_key: "ta_cle_api_jellyfin"
```

**Où trouver les clés API :**
- **Radarr** : Paramètres → Général → Clé API
- **Sonarr** : Paramètres → Général → Clé API
- **Jellyfin** : Tableau de bord → Clés API → + Ajouter une clé API

### 4. Lancer l'application

```bash
python main.py
```

L'interface est accessible sur `http://localhost:7979`

Au premier démarrage, `config.yaml` est automatiquement copié dans `data/config.json`. **Toutes les modifications ultérieures se font depuis l'interface web** (Paramètres). Le dossier `data/` contient toutes tes données persistantes — ne pas le supprimer lors des mises à jour.

---

## Structure des données

```
data/                    ← NE PAS supprimer lors des mises à jour
├── config.json          ← toute la configuration (URLs, clés, règles)
├── protected.json       ← whitelist films/séries protégés
└── purgearr.db          ← historique, queue, événements de visionnage
```

---

## Service systemd (Raspberry Pi)

```ini
[Unit]
Description=Purgearr Media Manager
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/srv/mergerfs/DATA_POOL/HDD 1TO/appdata/Purgearr
ExecStart=/usr/bin/python3 main.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable purgearr
sudo systemctl start purgearr
```

---

## Webhook Jellyfin (optionnel)

Installe le plugin **Webhook** depuis le catalogue Jellyfin, puis configure :
- **URL** : `http://[IP_DU_PI]:7979/webhook/jellyfin`
- **Événement** : `Playback Stop`

Sans webhook, un scan d'import manuel est disponible dans la sidebar.

---

## Mise à jour

```bash
git pull
# Redémarrer l'application
```

Le dossier `data/` n'est jamais touché par git — tes données sont préservées.

---

## Stack technique

- **Backend** : FastAPI + Uvicorn
- **Base de données** : SQLite (WAL mode) via SQLAlchemy
- **Scheduler** : APScheduler
- **Templates** : Jinja2
- **Frontend** : HTML/CSS/JS vanilla (pas de framework)

---

## Licence

MIT — utilise et adapte librement.
