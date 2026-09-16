import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Float, Integer, Boolean, DateTime, ForeignKey, Text, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow():
    return datetime.now(timezone.utc)


class Search(Base):
    __tablename__ = "searches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    zona: Mapped[str] = mapped_column(String(255), nullable=False)
    categoria: Mapped[str] = mapped_column(String(255), nullable=False)
    radio: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    included_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    target_leads: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fetch_mode: Mapped[str] = mapped_column(String(20), default="optimized")  # optimized|full
    include_with_website: Mapped[bool] = mapped_column(Boolean, default=False)
    min_rating: Mapped[float] = mapped_column(Float, default=4.3)
    max_days_since_review: Mapped[int] = mapped_column(Integer, default=90)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/running/done/failed
    total_candidates: Mapped[int] = mapped_column(Integer, default=0)
    total_leads: Mapped[int] = mapped_column(Integer, default=0)
    # Contadores de costo (investigacion.md:83)
    calls_cheap: Mapped[int] = mapped_column(Integer, default=0)      # Essentials (Text Search)
    calls_expensive: Mapped[int] = mapped_column(Integer, default=0)  # Enterprise+Atmosphere (Details)
    est_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    # Contadores de descarte (para ver dónde se filtra cada candidato)
    discarded_has_website: Mapped[int] = mapped_column(Integer, default=0)
    discarded_low_rating: Mapped[int] = mapped_column(Integer, default=0)
    discarded_no_recent_review: Mapped[int] = mapped_column(Integer, default=0)
    reused_from_cache: Mapped[int] = mapped_column(Integer, default=0)
    scored_leads: Mapped[int] = mapped_column(Integer, default=0)
    score_errors: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    leads: Mapped[list["Lead"]] = relationship("Lead", back_populates="search", cascade="all, delete-orphan")


class Lead(Base):
    __tablename__ = "leads"

    place_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    search_id: Mapped[str] = mapped_column(String(36), ForeignKey("searches.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    address: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(100), nullable=True)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    has_website: Mapped[bool] = mapped_column(Boolean, default=False)
    website_uri: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    maps_uri: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    last_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Semántica corregida: la API devuelve un set parcial de reviews (hasta 5 "most relevant").
    # recent_review_detected = hubo al menos una review reciente en el set devuelto.
    recent_review_detected: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    review_activity_confidence: Mapped[str | None] = mapped_column(String(10), nullable=True)  # full|partial|unknown
    reviews_returned: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Score LLM denormalizado (último score; historial en lead_scores)
    fit_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    intent: Mapped[str | None] = mapped_column(String(10), nullable=True)  # hot|warm|cold
    score_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    score_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="nuevo")  # nuevo/lista_contacto/contactado/descartado/convertido
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    search: Mapped["Search"] = relationship("Search", back_populates="leads")
    reviews: Mapped[list["ReviewSnapshot"]] = relationship("ReviewSnapshot", back_populates="lead", cascade="all, delete-orphan")


class ReviewSnapshot(Base):
    __tablename__ = "reviews_snapshot"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    place_id: Mapped[str] = mapped_column(String(255), ForeignKey("leads.place_id"), nullable=False)
    author: Mapped[str | None] = mapped_column(String(500), nullable=True)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    publish_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    lead: Mapped["Lead"] = relationship("Lead", back_populates="reviews")


class ApiClient(Base):
    __tablename__ = "api_clients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    api_key_hash: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ApiUsage(Base):
    """Log inmutable de cada llamada a Google Places (un row por request HTTP)."""
    __tablename__ = "api_usage"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    search_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("searches.id"), nullable=True)
    method: Mapped[str] = mapped_column(String(50))              # text_search | place_details
    sku: Mapped[str] = mapped_column(String(50))                 # text_search_enterprise | enterprise_atmosphere
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    month_key: Mapped[str] = mapped_column(String(7), index=True)  # YYYY-MM para cupos mensuales
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PlaceCache(Base):
    """Caché de Place Details para no re-pagar el SKU caro de place_ids ya vistos.

    Guarda la respuesta cruda de Google (serializable) + campos para skip temprano.
    """
    __tablename__ = "place_cache"

    place_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    website_uri: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details_json: Mapped[str] = mapped_column(Text, nullable=False)  # raw response de Google
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SearchCandidate(Base):
    """Candidatos crudos traídos por el Text Search (lo que devolvió Google, sin filtrar)."""
    __tablename__ = "search_candidates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    search_id: Mapped[str] = mapped_column(String(36), ForeignKey("searches.id"), nullable=False, index=True)
    place_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    formatted_address: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    has_website: Mapped[bool] = mapped_column(Boolean, default=False)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        # un place_id puede aparecer en varias búsquedas, pero no duplicado en la misma
        {"sqlite_autoincrement": False},
    )


class LeadScore(Base):
    """Historial de scores LLM por lead/modelo (auditabilidad + re-scoring)."""
    __tablename__ = "lead_scores"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    place_id: Mapped[str] = mapped_column(String(255), ForeignKey("leads.place_id"), nullable=False)
    search_id: Mapped[str] = mapped_column(String(36), ForeignKey("searches.id"), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    fit_score: Mapped[int] = mapped_column(Integer, nullable=False)
    intent: Mapped[str] = mapped_column(String(10), nullable=False)  # hot|warm|cold
    reason_codes: Mapped[str] = mapped_column(Text, nullable=True)  # JSON list
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WebhookDelivery(Base):
    """Registro de cada intento de notificación webhook al cerrar una corrida."""
    __tablename__ = "webhook_deliveries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    search_id: Mapped[str] = mapped_column(String(36), ForeignKey("searches.id"), nullable=False)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
