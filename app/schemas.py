from datetime import datetime
from pydantic import BaseModel, Field


class SearchCreate(BaseModel):
    zona: str = Field(..., examples=["Nueva Córdoba"])
    categoria: str = Field(..., examples=["gimnasios"])
    radio: int | None = Field(None, ge=100, le=50000, description="Radio en metros (requiere lat/lng)")
    lat: float | None = Field(None, ge=-90, le=90, description="Latitud del centro del radio")
    lng: float | None = Field(None, ge=-180, le=180, description="Longitud del centro del radio")
    included_type: str | None = Field(None, description="Tipo oficial de Google (override del mapeo por categoría)")
    target_leads: int | None = Field(None, ge=1, le=50, description="Cantidad de leads a buscar (máx 50 por petición)")
    fetch_mode: str = Field("optimized", pattern="^(optimized|full)$", description="optimized: 2 pasos barato+detalle; full: trae todo en el Text Search")
    include_with_website: bool = Field(False, description="True: incluye negocios con sitio web en los resultados")
    min_rating: float | None = Field(None, ge=0, le=5)
    max_days_since_review: int | None = Field(None, ge=1, le=365)


class SearchAccepted(BaseModel):
    """Acuse de recibo del POST /api/searches (202). El trabajo está encolado,
    no es el resultado. Los contadores se leen del GET /api/searches/{id}."""
    id: str
    zona: str
    categoria: str
    radio: int | None
    lat: float | None
    lng: float | None
    included_type: str | None
    target_leads: int | None
    fetch_mode: str
    include_with_website: bool
    min_rating: float
    max_days_since_review: int
    status: str  # siempre "pending"
    poll_url: str
    created_at: datetime


class SearchOut(BaseModel):
    id: str
    zona: str
    categoria: str
    radio: int | None
    lat: float | None
    lng: float | None
    included_type: str | None
    target_leads: int | None
    fetch_mode: str
    include_with_website: bool
    min_rating: float
    max_days_since_review: int
    status: str
    total_candidates: int
    total_leads: int
    calls_cheap: int
    calls_expensive: int
    est_cost_usd: float
    discarded_has_website: int
    discarded_low_rating: int
    discarded_no_recent_review: int
    reused_from_cache: int
    scored_leads: int
    score_errors: int
    error: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ReviewOut(BaseModel):
    author: str | None
    rating: int | None
    text: str | None
    publish_time: datetime | None

    class Config:
        from_attributes = True


class LeadOut(BaseModel):
    place_id: str
    search_id: str
    name: str
    address: str | None
    phone: str | None
    rating: float | None
    review_count: int | None
    has_website: bool
    website_uri: str | None
    maps_uri: str | None
    last_review_at: datetime | None
    # Semántica corregida: la API devuelve un set parcial de reviews.
    # recent_review_detected = se observó al menos una review reciente en el set devuelto.
    # review_activity_confidence: full | partial | unknown
    recent_review_detected: bool | None
    review_activity_confidence: str | None
    reviews_returned: int | None
    # Score LLM (zero-shot, OpenRouter)
    fit_score: int | None
    intent: str | None
    score_model: str | None
    scored_at: datetime | None
    score_error: str | None
    status: str
    reviews: list[ReviewOut] = []

    class Config:
        from_attributes = True


class LeadStatusUpdate(BaseModel):
    status: str = Field(..., pattern="^(nuevo|lista_contacto|contactado|descartado|convertido)$")


class LeadScoreOut(BaseModel):
    model: str
    fit_score: int
    intent: str
    reason_codes: list[str] = []
    reasoning: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class UsageRowOut(BaseModel):
    id: str
    method: str
    sku: str
    status_code: int | None
    latency_ms: int | None
    created_at: datetime

    class Config:
        from_attributes = True


class SkuTotalsOut(BaseModel):
    sku: str
    calls: int
    free: int
    chargeable: int
    cost: float


class UsageTotalsOut(BaseModel):
    month: str
    by_sku: list[SkuTotalsOut]
    total_cost: float


class SearchUsageOut(BaseModel):
    search_id: str
    calls_cheap: int
    calls_expensive: int
    est_cost_usd: float
    rows: list[UsageRowOut]
