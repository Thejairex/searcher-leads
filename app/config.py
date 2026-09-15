import os
from pathlib import Path
from pydantic_settings import BaseSettings


def _load_raw_env_key() -> str | None:
    """Soporta .env que contiene solo la key sin nombre de variable (caso detectado)."""
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        return None
    try:
        raw = env_path.read_text(encoding="utf-8").strip()
        # Si el archivo tiene solo una línea sin '=' y parece una API key de Google
        if raw and "=" not in raw and raw.startswith("AIza"):
            return raw
    except Exception:
        pass
    return None


class Settings(BaseSettings):
    database_url: str = "sqlite:///./data/search_leads.db"
    google_places_api_key: str = ""
    api_key: str = "dev-key-123"
    redis_url: str = "redis://localhost:6379/0"
    default_min_rating: float = 4.3
    default_max_days_since_review: int = 90

    # Google Places endpoints
    places_text_search_url: str = "https://places.googleapis.com/v1/places:searchText"
    places_details_url: str = "https://places.googleapis.com/v1/places/{place_id}"

    # SKUs y costos (pricing oficial Google, 2026-09)
    # Paso 1 = Text Search Enterprise (places.id,displayName,websiteUri) -> cupo 1k, exceso $35/1000
    # websiteUri pertenece al tier Enterprise (confirmado en sku-details de Google).
    free_ts_enterprise_monthly: int = 1000
    cost_ts_enterprise_per_1000: float = 35.0
    # Paso 2 = Place Details Enterprise+Atmosphere (rating,reviews) -> cupo 1k, exceso $25/1000
    free_enterprise_monthly: int = 1000
    cost_enterprise_per_1000: float = 25.0

    # Caché de detalles: no re-pagar PlaceDetails de place_ids ya vistos
    detail_cache_hours: int = 168  # 7 días

    # Robustez del cliente Places
    request_max_retries: int = 3            # reintentos ante 429/5xx
    request_retry_base_delay: float = 1.0   # backoff exponencial (1s, 2s, 4s)
    request_timeout: float = 20.0

    # Concurrency del worker (respetar QPS de Google)
    details_concurrency: int = 5

    # Búsquedas más precisas: si categoria tiene match en el catálogo Google,
    # manda includedType (filtro, no sube el SKU). Campo `included_type` en el
    # POST /api/searches hace override del mapeo automático.
    use_included_type: bool = True

    # Límite de candidatos a recolectar por búsqueda (Google Text Search cap ~60 por query)
    max_candidates: int = 300
    default_target_leads: int = 50

    # Webhook: notifica al cerrar una corrida (done o failed)
    webhook_url: str = ""
    webhook_enabled: bool = False
    webhook_timeout: float = 5.0

    # Scoring de leads vía OpenRouter (investigacion.md:198-208)
    openrouter_api_key: str = ""
    score_enabled: bool = True
    score_model: str = "openai/gpt-4.1-nano"
    score_fallback_model: str = "google/gemini-2.0-flash-lite-001"
    score_temperature: float = 0.0
    openrouter_url: str = "https://openrouter.ai/api/v1/chat/completions"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


def get_settings() -> Settings:
    s = Settings()
    # Fallback: .env con solo la key cruda
    if not s.google_places_api_key:
        raw = _load_raw_env_key()
        if raw:
            s.google_places_api_key = raw
        elif os.getenv("GOOGLE_PLACES_API_KEY"):
            s.google_places_api_key = os.getenv("GOOGLE_PLACES_API_KEY", "")
    # api_key fallback a GOOGLE key si no hay api_key seteada (dev mode)
    return s


settings = get_settings()
