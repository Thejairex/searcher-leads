from datetime import datetime, timezone, timedelta
from app.costing import (
    SKU_TS_ENTERPRISE,
    SKU_ENTERPRISE,
    month_key,
    monthly_totals,
    search_cost,
    SKU_FREE,
)
from app.db import SessionLocal, engine
from app.models import Search, ApiUsage
from app.config import settings
from sqlalchemy.orm import Session


def _clean_usage(db: Session):
    db.query(ApiUsage).delete()
    db.query(Search).delete()
    db.commit()


def test_month_key():
    assert month_key(datetime(2026, 9, 3, tzinfo=timezone.utc)) == "2026-09"
    assert month_key() == datetime.now().strftime("%Y-%m")


def test_monthly_totals_free():
    db = SessionLocal()
    _clean_usage(db)
    now = datetime.now(timezone.utc)
    db.add(ApiUsage(search_id=None, method="place_details", sku=SKU_ENTERPRISE, status_code=200, latency_ms=10, month_key=month_key(now)))
    db.commit()
    totals = monthly_totals(db)
    assert totals[SKU_ENTERPRISE]["calls"] == 1
    assert totals[SKU_ENTERPRISE]["chargeable"] == 0  # 1 < free 1000
    assert totals[SKU_ENTERPRISE]["cost"] == 0.0
    db.close()


def test_monthly_totals_over_quota():
    db = SessionLocal()
    _clean_usage(db)
    month = "2099-01"
    free = settings.free_enterprise_monthly
    extra = 500
    for _ in range(free + extra):
        db.add(ApiUsage(search_id=None, method="place_details", sku=SKU_ENTERPRISE, status_code=200, latency_ms=10, month_key=month))
    db.commit()
    totals = monthly_totals(db, month)
    info = totals[SKU_ENTERPRISE]
    assert info["chargeable"] == extra
    expected = round(extra * settings.cost_enterprise_per_1000 / 1000.0, 4)
    assert info["cost"] == expected
    db.close()


def test_search_cost_attr():
    db = SessionLocal()
    _clean_usage(db)
    month = "2099-02"
    free = settings.free_enterprise_monthly
    # 1000 gratis + 1000 extra en el mes
    for _ in range(free + 1000):
        db.add(ApiUsage(search_id="s1", method="place_details", sku=SKU_ENTERPRISE, status_code=200, latency_ms=10, month_key=month))
    db.commit()
    created = datetime(2099, 2, 3, tzinfo=timezone.utc)
    # esta corrida aporta 1000 de las 2000 del mes -> 50% de los 1000 cobrables
    cost = search_cost(db, "s1", search_cheap=0, search_expensive=1000, created_at=created)
    expected = round(1000 * settings.cost_enterprise_per_1000 / 1000.0 * 0.5, 4)
    assert cost == expected
    db.close()


def test_search_cost_within_quota_zero():
    db = SessionLocal()
    _clean_usage(db)
    month = "2099-03"
    db.add(ApiUsage(search_id="s1", method="place_details", sku=SKU_ENTERPRISE, status_code=200, latency_ms=10, month_key=month))
    db.commit()
    cost = search_cost(db, "s1", search_cheap=1, search_expensive=1, created_at=datetime(2099, 3, 1, tzinfo=timezone.utc))
    assert cost == 0.0
    db.close()


def test_ts_enterprise_sku_quota_and_price():
    """Text Search con websiteUri usa SKU 'text_search_enterprise': 1.000 gratis, $35/1000.

    Mapeo oficial (sku-details Google): websiteUri pertenece al tier Enterprise,
    no a Pro. Por eso el cupo es 1.000/mes y el exceso $35/1000.
    """
    db = SessionLocal()
    _clean_usage(db)
    month = "2099-04"
    free_ts = settings.free_ts_enterprise_monthly
    extra = 500
    for _ in range(free_ts + extra):
        db.add(ApiUsage(search_id=None, method="text_search", sku=SKU_TS_ENTERPRISE, status_code=200, latency_ms=10, month_key=month))
    db.commit()
    totals = monthly_totals(db, month)
    info = totals[SKU_TS_ENTERPRISE]
    assert info["calls"] == free_ts + extra
    assert info["free"] == 1000
    assert info["chargeable"] == extra
    expected = round(extra * settings.cost_ts_enterprise_per_1000 / 1000.0, 4)
    assert info["cost"] == expected
    assert settings.cost_ts_enterprise_per_1000 == 35.0
    db.close()