from contextlib import asynccontextmanager
import json
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, Query
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db, init_db
from app.models import Search, Lead, ApiUsage, LeadScore
from app.category_map import resolve_included_type
from app.schemas import (
    SearchCreate,
    SearchAccepted,
    SearchOut,
    LeadOut,
    LeadStatusUpdate,
    LeadScoreOut,
    UsageRowOut,
    SkuTotalsOut,
    UsageTotalsOut,
    SearchUsageOut,
)
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


@app.get("/api/searches/{search_id}/export.csv", dependencies=[Depends(verify_api_key)])
def export_search_csv(search_id: str, db: Session = Depends(get_db)):
    """Exporta los leads de la corrida a CSV (UTF-8 con BOM para Excel)."""
    s = db.query(Search).filter(Search.id == search_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Search not found")
    leads = db.query(Lead).filter(Lead.search_id == search_id).order_by(Lead.rating.desc().nullslast()).all()

    header = [
        "place_id", "name", "address", "phone", "rating", "review_count",
        "has_website", "website_uri", "maps_uri", "last_review_at",
        "recent_review_detected", "review_activity_confidence", "reviews_returned",
        "fit_score", "intent", "score_model", "status",
        "reviews", "reviews_total",
    ]
    import csv
    import io

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    for l in leads:
        review_texts = "; ".join((r.text or "").replace("\n", " ").replace(";", ",") for r in l.reviews if r.text)
        writer.writerow([
            l.place_id, l.name, l.address, l.phone, l.rating, l.review_count,
            l.has_website, l.website_uri, l.maps_uri,
            l.last_review_at.isoformat() if l.last_review_at else "",
            l.recent_review_detected, l.review_activity_confidence, l.reviews_returned,
            l.fit_score, l.intent, l.score_model, l.status,
            review_texts, len(l.reviews),
        ])
    # BOM UTF-8 para que Excel abra bien los acentos
    content = "\ufeff" + buf.getvalue()
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="searches_{search_id}.csv"'},
    )
