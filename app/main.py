from contextlib import asynccontextmanager
import json
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, Query
from fastapi.responses import JSONResponse, Response, FileResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db, init_db
from app.models import Search, Lead, ApiUsage, LeadScore, SearchCandidate, PlaceCache, ReviewSnapshot
from app.category_map import resolve_included_type
from app.schemas import (
    SearchCreate,
    SearchAccepted,
    SearchOut,
    LeadOut,
    LeadStatusUpdate,
    LeadScoreOut,
    CandidateOut,
    CandidateDetailOut,
    UsageRowOut,
    SkuTotalsOut,
    UsageTotalsOut,
    SearchUsageOut,
)
from app.places_client import PlacesClient
from app.security import verify_api_key
from app.tasks import run_search, score_search
from app.costing import monthly_totals, month_key


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="search-leads", version="0.1.0", lifespan=lifespan)


@app.get("/api/health")
def health():
    return {"status": "ok", "db": settings.database_url.split("://")[0]}


@app.get("/", include_in_schema=False)
def ui():
    """Consola web simple para consumir la API."""
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.post("/api/searches", response_model=SearchAccepted, status_code=202, dependencies=[Depends(verify_api_key)])
def create_search(payload: SearchCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    min_rating = payload.min_rating if payload.min_rating is not None else settings.default_min_rating
    max_days = payload.max_days_since_review if payload.max_days_since_review is not None else settings.default_max_days_since_review
    # included_type: override del usuario > mapeo por categoría (si está habilitado)
    included_type = payload.included_type
    if not included_type and settings.use_included_type:
        included_type = resolve_included_type(payload.categoria)

    search = Search(
        zona=payload.zona,
        categoria=payload.categoria,
        radio=payload.radio,
        lat=payload.lat,
        lng=payload.lng,
        included_type=included_type,
        target_leads=payload.target_leads,
        fetch_mode=payload.fetch_mode,
        include_with_website=payload.include_with_website,
        min_rating=min_rating,
        max_days_since_review=max_days,
        status="pending",
    )
    db.add(search)
    db.commit()
    db.refresh(search)

    # Dispara async en background (sin Redis/Celery para sqlite simple)
    background_tasks.add_task(run_search, search.id)

    poll_url = f"/api/searches/{search.id}"
    accepted = SearchAccepted(
        id=search.id,
        zona=search.zona,
        categoria=search.categoria,
        radio=search.radio,
        lat=search.lat,
        lng=search.lng,
        included_type=search.included_type,
        target_leads=search.target_leads,
        fetch_mode=search.fetch_mode,
        include_with_website=search.include_with_website,
        min_rating=search.min_rating,
        max_days_since_review=search.max_days_since_review,
        status=search.status,
        poll_url=poll_url,
        created_at=search.created_at,
    )
    # Header REST estándar para 202: dónde hacer polling del recurso
    return JSONResponse(content=accepted.model_dump(mode="json"), status_code=202, headers={"Location": poll_url})


@app.get("/api/searches/{search_id}", response_model=SearchOut, dependencies=[Depends(verify_api_key)])
def get_search(search_id: str, db: Session = Depends(get_db)):
    s = db.query(Search).filter(Search.id == search_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Search not found")
    return s


@app.get("/api/searches", response_model=list[SearchOut], dependencies=[Depends(verify_api_key)])
def list_searches(db: Session = Depends(get_db), limit: int = Query(20, le=100)):
    return db.query(Search).order_by(Search.created_at.desc()).limit(limit).all()


@app.get("/api/searches/{search_id}/leads", response_model=list[LeadOut], dependencies=[Depends(verify_api_key)])
def list_leads(
    search_id: str,
    db: Session = Depends(get_db),
    has_website: bool | None = None,
    min_rating: float | None = None,
    min_fit_score: int | None = Query(None, ge=0, le=100),
    intent: str | None = Query(None, pattern="^(hot|warm|cold)$"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    s = db.query(Search).filter(Search.id == search_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Search not found")
    q = db.query(Lead).filter(Lead.search_id == search_id)
    if has_website is not None:
        q = q.filter(Lead.has_website == has_website)
    if min_rating is not None:
        q = q.filter(Lead.rating >= min_rating)
    if min_fit_score is not None:
        q = q.filter(Lead.fit_score >= min_fit_score)
    if intent is not None:
        q = q.filter(Lead.intent == intent)
    q = q.order_by(Lead.rating.desc().nullslast())
    offset = (page - 1) * limit
    return q.offset(offset).limit(limit).all()


@app.get("/api/leads/export.csv", dependencies=[Depends(verify_api_key)])
def export_leads_csv(
    db: Session = Depends(get_db),
    has_website: bool | None = None,
    min_rating: float | None = None,
    min_fit_score: int | None = Query(None, ge=0, le=100),
    intent: str | None = Query(None, pattern="^(hot|warm|cold)$"),
    status: str | None = Query(None, pattern="^(nuevo|lista_contacto|contactado|descartado|convertido)$"),
):
    """Exporta TODOS los leads del pipeline (con filtros) a CSV (UTF-8 BOM).

    Incluye search_id/zona/categoria para rastrear el origen de cada lead.
    """
    q = db.query(Lead)
    if has_website is not None:
        q = q.filter(Lead.has_website == has_website)
    if min_rating is not None:
        q = q.filter(Lead.rating >= min_rating)
    if min_fit_score is not None:
        q = q.filter(Lead.fit_score >= min_fit_score)
    if intent is not None:
        q = q.filter(Lead.intent == intent)
    if status is not None:
        q = q.filter(Lead.status == status)
    leads = q.order_by(Lead.fit_score.desc().nullslast()).all()

    content = "\ufeff" + _leads_to_csv(leads)
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="leads.csv"'},
    )


@app.get("/api/searches/{search_id}/candidates", response_model=list[CandidateOut], dependencies=[Depends(verify_api_key)])
def list_candidates(
    search_id: str,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """Lista lo que trajo la búsqueda (candidatos crudos, sin consultar Details).

    Barato: solo DB, no toca Google.
    """
    s = db.query(Search).filter(Search.id == search_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Search not found")
    q = db.query(SearchCandidate).filter(SearchCandidate.search_id == search_id).order_by(SearchCandidate.position)
    offset = (page - 1) * limit
    return q.offset(offset).limit(limit).all()


@app.get("/api/candidates/{place_id}", response_model=CandidateDetailOut, dependencies=[Depends(verify_api_key)])
async def get_candidate_detail(
    place_id: str,
    db: Session = Depends(get_db),
    promote: bool = Query(False, description="Si true, intenta promover a lead si pasa filtros"),
    search_id: str | None = Query(None, description="search_id destino si promote=true"),
):
    """Trae los datos completos de un candidato (usa el SKU caro).

    Siempre usa el SKU caro (Enterprise+Atmosphere). Reusa PlaceCache si está fresco.
    """
    # 1. Cache hit?
    cached = db.query(PlaceCache).filter(PlaceCache.place_id == place_id).first()
    from datetime import datetime, timezone, timedelta
    raw = None
    used_cache = False
    if cached:
        fetched = cached.fetched_at
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - fetched < timedelta(hours=settings.detail_cache_hours):
            raw = json.loads(cached.details_json)
            used_cache = True
    if raw is None:
        from datetime import datetime as dt
        from app.costing import SKU_ENTERPRISE, month_key
        from app.models import ApiUsage
        import time
        # Uso desacoplado: sin search_id (global) -> month_key actual
        start = time.monotonic()
        status_code = None
        latency_ms = None
        try:
            client = PlacesClient()
            raw = await client.get_details(place_id)
            # Registrar api_usage como llamada desacoplada (search_id = None)
            # No podemos usar usage_cb porque es un cliente sin search; registramos directo
            pass
        except Exception:
            raise HTTPException(status_code=502, detail="No se pudo obtener el detalle del candidato desde Google")
        # Persistir en cache + api_usage
        parsed_for_cache = PlacesClient.parse_details(raw)
        if cached:
            cached.website_uri = parsed_for_cache["website_uri"]
            cached.rating = parsed_for_cache["rating"]
            cached.review_count = parsed_for_cache["review_count"]
            cached.details_json = json.dumps(raw, ensure_ascii=False)
            cached.fetched_at = datetime.now(timezone.utc)
        else:
            db.add(PlaceCache(
                place_id=place_id,
                website_uri=parsed_for_cache["website_uri"],
                rating=parsed_for_cache["rating"],
                review_count=parsed_for_cache["review_count"],
                details_json=json.dumps(raw, ensure_ascii=False),
                fetched_at=datetime.now(timezone.utc),
            ))
        db.commit()

    parsed = PlacesClient.parse_details(raw)
    confidence = PlacesClient.review_confidence(parsed.get("review_count"), parsed.get("reviews_returned") or 0)
    last = parsed.get("last_review_at")
    recent = last is not None  # sin ventana específica: si hay last_review_at, hubo review
    detail = CandidateDetailOut(
        place_id=parsed["place_id"],
        name=parsed["name"],
        address=parsed["address"],
        phone=parsed["phone"],
        rating=parsed["rating"],
        review_count=parsed["review_count"],
        has_website=parsed["has_website"],
        website_uri=parsed["website_uri"],
        maps_uri=parsed["maps_uri"],
        last_review_at=parsed["last_review_at"],
        review_activity_confidence=confidence,
        reviews_returned=parsed.get("reviews_returned"),
        recent_review_detected=recent,
        reviews=[{"author": r.get("author"), "rating": r.get("rating"), "text": r.get("text"), "publish_time": r.get("publish_time")} for r in parsed["reviews"]],
        cached=used_cache,
    )

    if promote and search_id:
        search = db.query(Search).filter(Search.id == search_id).first()
        if not search:
            raise HTTPException(status_code=404, detail="Search no encontrado para promover")
        reason = PlacesClient.filter_reason(parsed, search.min_rating, search.max_days_since_review)
        if reason == "has_website" and search.include_with_website:
            reason = None
        if reason is None:
            # upsert lead (mismo código que tasks)
            from datetime import datetime, timezone, timedelta
            review_confidence = PlacesClient.review_confidence(parsed.get("review_count"), parsed.get("reviews_returned") or 0)
            last2 = parsed.get("last_review_at")
            recent2 = last2 is not None and (datetime.now(timezone.utc) - last2).days <= search.max_days_since_review
            existing = db.query(Lead).filter(Lead.place_id == place_id).first()
            if existing:
                existing.search_id = search.id
                existing.name = parsed["name"]
                existing.address = parsed["address"]
                existing.phone = parsed["phone"]
                existing.rating = parsed["rating"]
                existing.review_count = parsed["review_count"]
                existing.has_website = parsed["has_website"]
                existing.website_uri = parsed["website_uri"]
                existing.maps_uri = parsed["maps_uri"]
                existing.last_review_at = parsed["last_review_at"]
                existing.recent_review_detected = recent2
                existing.review_activity_confidence = review_confidence
                existing.reviews_returned = parsed.get("reviews_returned")
                existing.updated_at = datetime.now(timezone.utc)
                db.query(ReviewSnapshot).filter(ReviewSnapshot.place_id == place_id).delete()
            else:
                db.add(Lead(
                    place_id=place_id, search_id=search.id,
                    name=parsed["name"], address=parsed["address"], phone=parsed["phone"],
                    rating=parsed["rating"], review_count=parsed["review_count"],
                    has_website=parsed["has_website"], website_uri=parsed["website_uri"],
                    maps_uri=parsed["maps_uri"], last_review_at=parsed["last_review_at"],
                    recent_review_detected=recent2, review_activity_confidence=review_confidence,
                    reviews_returned=parsed.get("reviews_returned"),
                ))
                db.flush()
            for r in parsed["reviews"]:
                from app.models import ReviewSnapshot as RS
                db.add(RS(place_id=place_id, author=r.get("author"), rating=r.get("rating"), text=r.get("text"), publish_time=r.get("publish_time")))
            db.commit()

    return detail


@app.get("/api/leads/{place_id}", response_model=LeadOut, dependencies=[Depends(verify_api_key)])
def get_lead(place_id: str, db: Session = Depends(get_db)):
    lead = db.query(Lead).filter(Lead.place_id == place_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


@app.get("/api/leads/{place_id}/scores", response_model=list[LeadScoreOut], dependencies=[Depends(verify_api_key)])
def get_lead_scores(place_id: str, db: Session = Depends(get_db)):
    """Historial de scores LLM del lead (auditabilidad + re-scoring).

    Incluye model, fit_score, intent, reason_codes, reasoning y created_at.
    """
    lead = db.query(Lead).filter(Lead.place_id == place_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    rows = (
        db.query(LeadScore)
        .filter(LeadScore.place_id == place_id)
        .order_by(LeadScore.created_at.desc())
        .all()
    )
    scores = []
    for r in rows:
        try:
            reason_codes = json.loads(r.reason_codes) if r.reason_codes else []
        except Exception:
            reason_codes = []
        scores.append(
            LeadScoreOut(
                model=r.model,
                fit_score=r.fit_score,
                intent=r.intent,
                reason_codes=reason_codes,
                reasoning=r.reasoning,
                created_at=r.created_at,
            )
        )
    return scores


@app.post("/api/leads/{place_id}/status", response_model=LeadOut, dependencies=[Depends(verify_api_key)])
def update_lead_status(place_id: str, payload: LeadStatusUpdate, db: Session = Depends(get_db)):
    lead = db.query(Lead).filter(Lead.place_id == place_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    lead.status = payload.status
    db.commit()
    db.refresh(lead)
    return lead


@app.get("/api/usage", response_model=UsageTotalsOut, dependencies=[Depends(verify_api_key)])
def get_usage(db: Session = Depends(get_db), month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$")):
    """Totales mensuales por SKU: barato (Essentials) vs caro (Enterprise+Atmosphere)."""
    month = month or month_key()
    totals = monthly_totals(db, month)
    by_sku = [
        SkuTotalsOut(sku=sku, **info)
        for sku, info in sorted(totals.items())
    ]
    # SKUs sin llamadas también se muestran (para ver cupos disponibles)
    known = {t.sku for t in by_sku}
    for sku, free in (("text_search_enterprise", settings.free_ts_enterprise_monthly), ("enterprise_atmosphere", settings.free_enterprise_monthly)):
        if sku not in known:
            by_sku.append(SkuTotalsOut(sku=sku, calls=0, free=free, chargeable=0, cost=0.0))
    return UsageTotalsOut(month=month, by_sku=by_sku, total_cost=round(sum(t.cost for t in by_sku), 4))


@app.get("/api/searches/{search_id}/usage", response_model=SearchUsageOut, dependencies=[Depends(verify_api_key)])
def get_search_usage(search_id: str, db: Session = Depends(get_db)):
    """Desglose de llamadas de una corrida + costo estimado."""
    s = db.query(Search).filter(Search.id == search_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Search not found")
    rows = (
        db.query(ApiUsage)
        .filter(ApiUsage.search_id == search_id)
        .order_by(ApiUsage.created_at)
        .all()
    )
    return SearchUsageOut(
        search_id=search_id,
        calls_cheap=s.calls_cheap,
        calls_expensive=s.calls_expensive,
        est_cost_usd=s.est_cost_usd,
        rows=[UsageRowOut.model_validate(r) for r in rows],
    )


@app.post("/api/searches/{search_id}/score", response_model=SearchOut, status_code=202, dependencies=[Depends(verify_api_key)])
def rescore_search(search_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Fuerza re-scoring LLM de todos los leads de la corrida (nuevas filas en lead_scores)."""
    s = db.query(Search).filter(Search.id == search_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Search not found")
    if not settings.score_enabled or not settings.openrouter_api_key:
        raise HTTPException(status_code=409, detail="Scoring deshabilitado o OPENROUTER_API_KEY no configurada")
    background_tasks.add_task(score_search, search_id)
    return s


def _leads_to_csv(leads: list[Lead]) -> str:
    """Genera el contenido CSV (sin BOM) para una lista de leads."""
    import csv
    import io

    header = [
        "place_id", "search_id", "zona", "categoria", "name", "address", "phone",
        "rating", "review_count", "has_website", "website_uri", "maps_uri",
        "last_review_at", "recent_review_detected", "review_activity_confidence",
        "reviews_returned", "fit_score", "intent", "score_model", "status",
        "reviews", "reviews_total",
    ]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    for l in leads:
        search = l.search
        review_texts = "; ".join((r.text or "").replace("\n", " ").replace(";", ",") for r in l.reviews if r.text)
        writer.writerow([
            l.place_id, l.search_id, search.zona if search else "", search.categoria if search else "",
            l.name, l.address, l.phone, l.rating, l.review_count,
            l.has_website, l.website_uri, l.maps_uri,
            l.last_review_at.isoformat() if l.last_review_at else "",
            l.recent_review_detected, l.review_activity_confidence, l.reviews_returned,
            l.fit_score, l.intent, l.score_model, l.status,
            review_texts, len(l.reviews),
        ])
    return buf.getvalue()


@app.get("/api/searches/{search_id}/export.csv", dependencies=[Depends(verify_api_key)])
def export_search_csv(search_id: str, db: Session = Depends(get_db)):
    """Exporta los leads de la corrida a CSV (UTF-8 con BOM para Excel)."""
    s = db.query(Search).filter(Search.id == search_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Search not found")
    leads = db.query(Lead).filter(Lead.search_id == search_id).order_by(Lead.rating.desc().nullslast()).all()
    content = "\ufeff" + _leads_to_csv(leads)
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="searches_{search_id}.csv"'},
    )
