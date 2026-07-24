from datetime import datetime
from pathlib import Path
from sqlalchemy import Column, DateTime, Float, Integer, String, Text, create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

_DATA_DIR = Path(__file__).parent / "data"
_DATA_DIR.mkdir(exist_ok=True)
DATABASE_URL = f"sqlite:///{_DATA_DIR / 'purgearr.db'}"
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30},
)

@event.listens_for(engine, "connect")
def _set_wal_mode(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()

SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class WatchEvent(Base):
    """Un utilisateur a regardé un item jusqu'à un certain pourcentage."""
    __tablename__ = "watch_events"

    id = Column(Integer, primary_key=True)
    jellyfin_item_id = Column(String, nullable=False, index=True)
    jellyfin_user_id = Column(String, nullable=False)
    user_name = Column(String)
    item_type = Column(String)       # "Movie" ou "Episode"
    item_title = Column(String)
    series_title = Column(String)    # rempli si Episode
    season = Column(Integer)
    episode = Column(Integer)
    percentage = Column(Float, default=0.0)
    watched_at = Column(DateTime, default=datetime.utcnow)


class DeletionQueue(Base):
    """Items en attente de suppression (délai configuré)."""
    __tablename__ = "deletion_queue"

    id = Column(Integer, primary_key=True)
    jellyfin_item_id = Column(String, nullable=False, index=True)
    item_type = Column(String)
    item_title = Column(String)
    series_title = Column(String)
    tmdb_id = Column(String)
    tvdb_id = Column(String)
    imdb_id = Column(String)
    file_path = Column(String)
    scheduled_at = Column(DateTime, nullable=False)
    status = Column(String, default="pending")   # pending | processing | done | failed
    created_at = Column(DateTime, default=datetime.utcnow)


class DeletionHistory(Base):
    """Historique de toutes les suppressions effectuées."""
    __tablename__ = "deletion_history"

    id = Column(Integer, primary_key=True)
    jellyfin_item_id = Column(String)
    item_type = Column(String)
    item_title = Column(String)
    series_title = Column(String)
    deleted_at = Column(DateTime, default=datetime.utcnow)
    deleted_from = Column(Text)      # JSON: ["radarr", "transmission", "jellyfin"]
    triggered_by = Column(String)    # user_id ou "scheduler"
    error = Column(Text)             # message d'erreur si échec partiel
    details_json = Column(Text)      # JSON: {file_path, file_size_bytes, file_size_human, torrents, copies_deleted, copies_size_human, total_freed_human}


class LogEntry(Base):
    """Journal événementiel catégorisé (deletion, watch, protection, sync, etc.)."""
    __tablename__ = "event_logs"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    level = Column(String, index=True)     # info | warning | error
    category = Column(String, index=True)  # deletion | watch | queue | protection | sync | scheduler | webhook | service | config | error
    message = Column(Text)
    context = Column(Text)                 # JSON facultatif (métadonnées supplémentaires)


def init_db():
    Base.metadata.create_all(engine)


def migrate_db():
    """Ajoute les colonnes manquantes aux tables existantes (idempotent)."""
    from sqlalchemy import text
    with engine.connect() as conn:
        for col_def in ["details_json TEXT"]:
            try:
                conn.execute(text(f"ALTER TABLE deletion_history ADD COLUMN {col_def}"))
                conn.commit()
            except Exception:
                pass  # colonne déjà présente


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
