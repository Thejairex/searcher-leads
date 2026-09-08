import hashlib
from fastapi import Header, HTTPException, Depends
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import ApiClient


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def verify_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: Session = Depends(get_db),
):
    # Dev mode: settings.api_key como fallback sin necesidad de tabla
    candidates = []
    if settings.api_key:
        candidates.append(settings.api_key)
    # También acepta GOOGLE key como api key en dev si no hay otra
    if settings.google_places_api_key and settings.google_places_api_key not in candidates:
        # no usar google key como auth, solo si api_key no está seteado
        pass

    if x_api_key is None:
        raise HTTPException(status_code=401, detail="Missing X-API-Key")

    # Check static key first
    if x_api_key in candidates:
        return x_api_key

    # Check DB
    h = hash_key(x_api_key)
    exists = db.query(ApiClient).filter(ApiClient.api_key_hash == h).first()
    if exists:
        return x_api_key

    raise HTTPException(status_code=401, detail="Invalid API key")
