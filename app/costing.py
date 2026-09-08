"""
Cálculo de costo según cupos gratis mensuales por SKU (pricing oficial Google).

Modelo:
- Cada SKU tiene cupo gratis mensual independiente (Text Search Enterprise 1k, Details Ent+Atmos 1k).
- Lo que excede el cupo se cobra por 1.000 llamadas (Text Search Enterprise $35, Details $25).
- Para no depender del orden de corridas, el costo de una corrida se atribuye
  proporcionalmente a su share de llamadas del mes.
"""
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ApiUsage

# Text Search con places.websiteUri (Paso 1) -> tier Enterprise (websiteUri activa Enterprise)
SKU_TS_ENTERPRISE = "text_search_enterprise"
# Place Details con rating/reviews (Paso 2) -> Enterprise + Atmosphere
SKU_ENTERPRISE = "enterprise_atmosphere"

SKU_FREE = {
    SKU_TS_ENTERPRISE: settings.free_ts_enterprise_monthly,
    SKU_ENTERPRISE: settings.free_enterprise_monthly,
}
SKU_UNIT_PRICE = {
    SKU_TS_ENTERPRISE: settings.cost_ts_enterprise_per_1000,
    SKU_ENTERPRISE: settings.cost_enterprise_per_1000,
}


def month_key(dt: datetime | None = None) -> str:
    dt = dt or datetime.now()
    return dt.strftime("%Y-%m")


def monthly_totals(db: Session, month: str | None = None) -> dict[str, dict]:
    """Agrega llamadas por SKU en un mes. Retorna {sku: {calls, free, chargeable, cost}}."""
    month = month or month_key()
    rows = (
        db.query(ApiUsage.sku, func.count(ApiUsage.id))
        .filter(ApiUsage.month_key == month)
        .group_by(ApiUsage.sku)
        .all()
    )
    totals: dict[str, dict] = {}
    for sku, calls in rows:
        free = SKU_FREE.get(sku, 0)
        chargeable = max(0, calls - free)
        cost = round(chargeable * SKU_UNIT_PRICE.get(sku, 0) / 1000.0, 4)
        totals[sku] = {"calls": calls, "free": free, "chargeable": chargeable, "cost": cost}
    return totals


def search_cost(db: Session, search_id: str, search_cheap: int, search_expensive: int, created_at: datetime) -> float:
    """Costo estimado de una corrida, atribuido proporcionalmente por SKU dentro del mes."""
    month = month_key(created_at)
    totals = monthly_totals(db, month)
    cost = 0.0
    for sku, calls in ((SKU_TS_ENTERPRISE, search_cheap), (SKU_ENTERPRISE, search_expensive)):
        if calls <= 0:
            continue
        info = totals.get(sku)
        if not info:
            continue
        month_calls = info["calls"]
        if month_calls <= info["free"]:
            continue
        chargeable = month_calls - info["free"]
        # share de esta corrida sobre el total del mes
        share = calls / month_calls if month_calls else 0
        cost += chargeable * SKU_UNIT_PRICE[sku] / 1000.0 * share
    return round(cost, 4)