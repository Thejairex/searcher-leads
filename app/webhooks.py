"""Notificación webhook al cerrar una corrida (done o failed).

Aislado: un fallo del webhook no debe romper la corrida ni el worker.
Cada intento se registra en webhook_deliveries para trazabilidad.
"""
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.db import SessionLocal
from app.models import Search, WebhookDelivery


async def deliver_webhook(search_id: str) -> None:
    """Envía el webhook de fin de corrida si está configurado y habilitado.

    Recibe el search_id y re-consulta en su propia sesión (evita objetos detached).
    """
    if not (settings.webhook_enabled and settings.webhook_url):
        return

    db = SessionLocal()
    try:
        search = db.query(Search).filter(Search.id == search_id).first()
        if not search:
            return

        payload = {
            "search_id": search.id,
            "status": search.status,
            "zona": search.zona,
            "categoria": search.categoria,
            "total_candidates": search.total_candidates,
            "total_leads": search.total_leads,
            "scored_leads": search.scored_leads,
            "score_errors": search.score_errors,
            "est_cost_usd": search.est_cost_usd,
            "error": search.error,
            "leads_url": f"/api/searches/{search.id}/leads",
            "csv_url": f"/api/searches/{search.id}/export.csv",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

        try:
            async with httpx.AsyncClient(timeout=settings.webhook_timeout) as client:
                resp = await client.post(settings.webhook_url, json=payload)
            db.add(
                WebhookDelivery(
                    search_id=search_id,
                    url=settings.webhook_url,
                    status_code=resp.status_code,
                    success=200 <= resp.status_code < 300,
                    attempts=1,
                    response_body=resp.text[:2000] if resp.text else None,
                )
            )
            db.commit()
        except Exception as e:  # noqa: BLE001 - aislado del flujo principal
            db.add(
                WebhookDelivery(
                    search_id=search_id,
                    url=settings.webhook_url,
                    status_code=None,
                    success=False,
                    attempts=1,
                    response_body=str(e)[:2000],
                )
            )
            db.commit()
    finally:
        db.close()