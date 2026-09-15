from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    pass


# SQLite necesita check_same_thread=False para uso con FastAPI
connect_args = {}
if settings.database_url.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(settings.database_url, connect_args=connect_args, echo=False)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _ensure_search_columns():
    """SQLite no soporta ALTER para agregar columnas en create_all; migra manualmente."""
    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(searches)")).fetchall()}
        # Rename semántico ANTES de agregar la nueva: discarded_old_review -> discarded_no_recent_review
        if "discarded_old_review" in cols and "discarded_no_recent_review" not in cols:
            conn.execute(text("ALTER TABLE searches RENAME COLUMN discarded_old_review TO discarded_no_recent_review"))
            cols = {row[1] for row in conn.execute(text("PRAGMA table_info(searches)")).fetchall()}
        # Estado intermedio de una versión buggy: ambas columnas presentes -> consolidar y dropear la vieja
        if "discarded_old_review" in cols and "discarded_no_recent_review" in cols:
            conn.execute(text("UPDATE searches SET discarded_no_recent_review = discarded_old_review WHERE discarded_old_review != 0"))
            conn.execute(text("ALTER TABLE searches DROP COLUMN discarded_old_review"))
            cols = {row[1] for row in conn.execute(text("PRAGMA table_info(searches)")).fetchall()}
        adds = {
            "calls_cheap": "INTEGER DEFAULT 0",
            "calls_expensive": "INTEGER DEFAULT 0",
            "est_cost_usd": "FLOAT DEFAULT 0",
            "discarded_has_website": "INTEGER DEFAULT 0",
            "discarded_low_rating": "INTEGER DEFAULT 0",
            "discarded_no_recent_review": "INTEGER DEFAULT 0",
            "reused_from_cache": "INTEGER DEFAULT 0",
            "scored_leads": "INTEGER DEFAULT 0",
            "score_errors": "INTEGER DEFAULT 0",
            "lat": "FLOAT",
            "lng": "FLOAT",
            "included_type": "VARCHAR(100)",
            "target_leads": "INTEGER",
            "fetch_mode": "VARCHAR(20) DEFAULT 'optimized'",
            "include_with_website": "BOOLEAN DEFAULT 0",
        }
        for name, ddl in adds.items():
            if name not in cols:
                conn.execute(text(f"ALTER TABLE searches ADD COLUMN {name} {ddl}"))
        conn.commit()


def _ensure_lead_columns():
    """Columnas de semántica de reviews en leads (set parcial de la API)."""
    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(leads)")).fetchall()}
        adds = {
            "recent_review_detected": "BOOLEAN",
            "review_activity_confidence": "VARCHAR(10)",
            "reviews_returned": "INTEGER",
            "fit_score": "INTEGER",
            "intent": "VARCHAR(10)",
            "score_model": "VARCHAR(100)",
            "scored_at": "DATETIME",
            "score_error": "TEXT",
        }
        for name, ddl in adds.items():
            if name not in cols:
                conn.execute(text(f"ALTER TABLE leads ADD COLUMN {name} {ddl}"))
        conn.commit()


def _migrate_usage_sku():
    """Filas viejas usaban 'essentials'/'pro' para Text Search; el SKU correcto es 'text_search_enterprise'."""
    with engine.connect() as conn:
        conn.execute(
            text("UPDATE api_usage SET sku = 'text_search_enterprise' WHERE method = 'text_search' AND sku IN ('essentials', 'pro')")
        )
        conn.commit()


def init_db():
    import app.models  # noqa: F401 ensure models registered

    Base.metadata.create_all(bind=engine)
    _ensure_search_columns()
    _ensure_lead_columns()
    _migrate_usage_sku()
