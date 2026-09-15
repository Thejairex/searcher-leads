import asyncio

import json
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import Search, Lead, ReviewSnapshot, ApiUsage, PlaceCache, LeadScore
from app.places_client import PlacesClient
from app.scoring_client import ScoringClient
from app.costing import SKU_TS_ENTERPRISE, SKU_ENTERPRISE, month_key, search_cost
from app.webhooks import deliver_webhook


def _get_db() -> Session:
    return SessionLocal()


async def _run_search_async(search_id: str, client: PlacesClient | None = None):
    db = _get_db()
    try:
        search = db.query(Search).filter(Search.id == search_id).first()
        if not search:
            return
        search.status = "running"
        db.commit()

        usage_rows: list[ApiUsage] = []

        def record_usage(method: str, sku: str, status_code: int | None, latency_ms: int | None):
            usage_rows.append(
                ApiUsage(
                    search_id=search_id,
                    method=method,
                    sku=sku,
                    status_code=status_code,
                    latency_ms=latency_ms,
                    month_key=month_key(),
                )
            )

        client = client or PlacesClient(usage_cb=record_usage)

        # Paso 1 (Text Search Enterprise/Enterprise+Atmosphere): candidatos.
        # optimized -> solo id/websiteUri (barato) + Details después.
        # full      -> todos los campos ya en el Text Search (sin Details).
        # Google ya descartó rating < min_rating (minRating server-side).
        candidates = await client.text_search_all(
            search.zona,
            search.categoria,
            max_pages=3,
            min_rating=search.min_rating,
            lat=search.lat,
            lng=search.lng,
            radio=search.radio,
            included_type=search.included_type,
            fetch_mode=search.fetch_mode,
        )
        search.total_candidates = len(candidates)
        db.commit()

        cache_window = timedelta(hours=settings.detail_cache_hours)

        # Contadores agregados por el fan-out (evita carreras con una sola sesión)
        aggregate = {
            "leads_found": 0,
            "discarded_has_website": 0,
            "discarded_low_rating": 0,
            "discarded_no_recent_review": 0,
            "reused_from_cache": 0,
        }

        sem = asyncio.Semaphore(settings.details_concurrency)

        async def procesar_candidato(cand: dict):
            pid = cand["place_id"]
            async with sem:
                try:
                    # Skip temprano por web (solo si NO queremos incluir con web)
                    if cand.get("has_website") and not search.include_with_website:
                        aggregate["discarded_has_website"] += 1
                        return None

                    # fetch_mode="full": el Text Search ya trajo todos los datos
                    parsed = cand.get("parsed")
                    from_cache = False
                    if parsed is None:
                        # Paso 2 (Enterprise+Atmosphere): detail caro, solo si hace falta.
                        # Caché: si ya tenemos el detalle fresco, no re-pagamos el SKU.
                        cached = db.get(PlaceCache, pid)
                        if cached:
                            fetched = cached.fetched_at
                            if fetched.tzinfo is None:
                                fetched = fetched.replace(tzinfo=timezone.utc)
                            if datetime.now(timezone.utc) - fetched < cache_window:
                                parsed = PlacesClient.parse_details(json.loads(cached.details_json))
                                from_cache = True

                        if parsed is None:
                            raw = await client.get_details(pid)
                            parsed = PlacesClient.parse_details(raw)
                            # Guardar caché SIEMPRE (incluso descartados) para futuras corridas.
                            if cached:
                                cached.website_uri = parsed["website_uri"]
                                cached.rating = parsed["rating"]
                                cached.review_count = parsed["review_count"]
                                cached.details_json = json.dumps(raw, ensure_ascii=False)
                                cached.fetched_at = datetime.now(timezone.utc)
                            else:
                                db.add(
                                    PlaceCache(
                                        place_id=pid,
                                        website_uri=parsed["website_uri"],
                                        rating=parsed["rating"],
                                        review_count=parsed["review_count"],
                                        details_json=json.dumps(raw, ensure_ascii=False),
                                        fetched_at=datetime.now(timezone.utc),
                                    )
                                )
                                db.flush()

                    # Filtros de calidad
                    # web: si include_with_website=True, no descartamos por web
                    reason = PlacesClient.filter_reason(parsed, search.min_rating, search.max_days_since_review)
                    if reason == "has_website" and search.include_with_website:
                        reason = None  # el usuario pidió incluir con web
                    if reason == "low_rating":
                        aggregate["discarded_low_rating"] += 1
                        db.commit()
                        return None
                    if reason == "no_recent_review":
                        aggregate["discarded_no_recent_review"] += 1
                        db.commit()
                        return None

                    if from_cache:
                        aggregate["reused_from_cache"] += 1

                    # Semántica corregida: no afirmamos actividad real, solo que la API devolvió
                    # al menos una review reciente dentro de la ventana (el set es parcial, hasta 5).
                    review_confidence = PlacesClient.review_confidence(parsed.get("review_count"), parsed.get("reviews_returned") or 0)
                    last = parsed.get("last_review_at")
                    recent_review_detected = last is not None and (datetime.now(timezone.utc) - last).days <= search.max_days_since_review

                    # Persistir Lead (upsert por place_id, dedupe)
                    existing = db.query(Lead).filter(Lead.place_id == pid).first()
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
                        existing.recent_review_detected = recent_review_detected
                        existing.review_activity_confidence = review_confidence
                        existing.reviews_returned = parsed.get("reviews_returned")
                        existing.updated_at = datetime.now(timezone.utc)
                        lead = existing
                        db.query(ReviewSnapshot).filter(ReviewSnapshot.place_id == pid).delete()
                    else:
                        lead = Lead(
                            place_id=pid,
                            search_id=search.id,
                            name=parsed["name"],
                            address=parsed["address"],
                            phone=parsed["phone"],
                            rating=parsed["rating"],
                            review_count=parsed["review_count"],
                            has_website=parsed["has_website"],
                            website_uri=parsed["website_uri"],
                            maps_uri=parsed["maps_uri"],
                            last_review_at=parsed["last_review_at"],
                            recent_review_detected=recent_review_detected,
                            review_activity_confidence=review_confidence,
                            reviews_returned=parsed.get("reviews_returned"),
                        )
                        db.add(lead)
                        db.flush()

                    for r in parsed["reviews"]:
                        db.add(
                            ReviewSnapshot(
                                place_id=pid,
                                author=r.get("author"),
                                rating=r.get("rating"),
                                text=r.get("text"),
                                publish_time=r.get("publish_time"),
                            )
                        )
                    aggregate["leads_found"] += 1
                    db.commit()
                    return True
                except Exception as e:
                    print(f"[search {search_id}] error place {pid}: {e}")
                    db.rollback()
                    return None

        # Fan-out concurrente: un semáforo limita las llamadas simultáneas a Google
        await asyncio.gather(*(procesar_candidato(c) for c in candidates))

        # Persistir log de uso + contadores de costo
        calls_cheap = sum(1 for r in usage_rows if r.sku == SKU_TS_ENTERPRISE)
        calls_expensive = sum(1 for r in usage_rows if r.sku == SKU_ENTERPRISE)
        for row in usage_rows:
            db.add(row)
        search.calls_cheap = calls_cheap
        search.calls_expensive = calls_expensive
        search.est_cost_usd = search_cost(db, search_id, calls_cheap, calls_expensive, search.created_at)
        search.discarded_has_website = aggregate["discarded_has_website"]
        search.discarded_low_rating = aggregate["discarded_low_rating"]
        search.discarded_no_recent_review = aggregate["discarded_no_recent_review"]
        search.reused_from_cache = aggregate["reused_from_cache"]
        search.total_leads = aggregate["leads_found"]
        search.status = "done"
        search.updated_at = datetime.now(timezone.utc)
        db.commit()

        # Notificar fin de corrida (aislado: un fallo del webhook no rompe nada)
        try:
            await deliver_webhook(search.id)
        except Exception:
            pass
    except Exception as e:
        db.rollback()
        s = db.query(Search).filter(Search.id == search_id).first()
        if s:
            s.status = "failed"
            s.error = str(e)[:2000]
            db.commit()
            try:
                await deliver_webhook(s.id)
            except Exception:
                pass
        print(f"[search {search_id}] failed: {e}")
    finally:
        db.close()


def run_search(search_id: str):
    """Entry point sincrónico para BackgroundTasks / Celery."""
    asyncio.run(_run_search_async(search_id))
    if settings.score_enabled:
        asyncio.run(_score_search_async(search_id))


async def _score_search_async(search_id: str, scoring_client: ScoringClient | None = None):
    """Scoring LLM post-procesamiento. Aislado: errores no marcan la corrida como failed."""
    if not settings.score_enabled:
        return
    db = _get_db()
    try:
        search = db.query(Search).filter(Search.id == search_id).first()
        if not search:
            return
        client = scoring_client or ScoringClient()
        if isinstance(client, ScoringClient) and not client.api_key:
            return

        leads = db.query(Lead).filter(Lead.search_id == search_id).all()
        scored = 0
        errors = 0
        for lead in leads:
            try:
                lead_data = {
                    "nombre": lead.name,
                    "categoria": search.categoria,
                    "zona": search.zona,
                    "has_website": lead.has_website,
                    "rating": lead.rating,
                    "review_count": lead.review_count,
                    "last_review_at": lead.last_review_at,
                    "review_texts": [r.text for r in lead.reviews if r.text],
                }
                result, model_used, err = await client.score_lead(lead_data)
                if result is None:
                    errors += 1
                    lead.score_error = (err or "score falló (ambos modelos)")[:2000]
                    db.commit()
                    continue

                db.add(
                    LeadScore(
                        place_id=lead.place_id,
                        search_id=search_id,
                        model=model_used,
                        fit_score=result.fit_score,
                        intent=result.intent,
                        reason_codes=json.dumps(result.sorted_reason_codes(), ensure_ascii=False),
                        reasoning=result.reasoning,
                        raw_response=result.model_dump_json(),
                    )
                )
                lead.fit_score = result.fit_score
                lead.intent = result.intent
                lead.score_model = model_used
                lead.scored_at = datetime.now(timezone.utc)
                lead.score_error = None
                # Alcance (c): auto-marcar "lista_contacto" si hot y nadie lo tocó aún
                if result.intent == "hot" and lead.status == "nuevo":
                    lead.status = "lista_contacto"
                scored += 1
                db.commit()
            except Exception as e:
                errors += 1
                lead.score_error = str(e)[:2000]
                db.rollback()
                continue

        search.scored_leads = scored
        search.score_errors = errors
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[search {search_id}] scoring failed: {e}")
    finally:
        db.close()


def score_search(search_id: str):
    """Rescoring manual (POST /api/searches/{id}/score) -> BackgroundTasks."""
    asyncio.run(_score_search_async(search_id))