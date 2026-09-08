from fastapi.testclient import TestClient
from app.main import app
from app.config import settings
from app.db import SessionLocal
from app.models import Search, ApiUsage

client = TestClient(app)
H = {"X-API-Key": settings.api_key}


def test_usage_endpoint():
    r = client.get("/api/usage", headers=H)
    assert r.status_code == 200
    data = r.json()
    assert data["month"]
    skus = {s["sku"] for s in data["by_sku"]}
    assert "text_search_enterprise" in skus
    assert "enterprise_atmosphere" in skus
    for s in data["by_sku"]:
        assert s["free"] > 0
        assert s["cost"] >= 0


def test_usage_endpoint_auth():
    r = client.get("/api/usage")
    assert r.status_code == 401


def test_search_usage_endpoint():
    db = SessionLocal()
    s = Search(zona="Z", categoria="C", min_rating=4.3, max_days_since_review=90, status="done",
               calls_cheap=1, calls_expensive=3, est_cost_usd=0.0)
    db.add(s)
    db.commit()
    db.refresh(s)
    db.add(ApiUsage(search_id=s.id, method="text_search", sku="text_search_enterprise", status_code=200, latency_ms=50, month_key="2099-01"))
    db.commit()
    sid = s.id
    db.close()

    r = client.get(f"/api/searches/{sid}/usage", headers=H)
    assert r.status_code == 200
    data = r.json()
    assert data["search_id"] == sid
    assert data["calls_cheap"] == 1
    assert data["calls_expensive"] == 3
    assert len(data["rows"]) == 1
    assert data["rows"][0]["sku"] == "text_search_enterprise"


def test_search_usage_404():
    r = client.get("/api/searches/no-existe/usage", headers=H)
    assert r.status_code == 404


def test_search_out_includes_usage():
    db = SessionLocal()
    s = Search(zona="Z", categoria="C", status="pending")
    db.add(s)
    db.commit()
    db.refresh(s)
    sid = s.id
    db.close()
    r = client.get(f"/api/searches/{sid}", headers=H)
    data = r.json()
    assert "calls_cheap" in data
    assert "calls_expensive" in data
    assert "est_cost_usd" in data