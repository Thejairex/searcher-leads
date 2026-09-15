from fastapi.testclient import TestClient
from app.main import app
from app.config import settings

# Use configured API key (dev-key-123) for tests
API_KEY = settings.api_key or "dev-key-123"
client = TestClient(app)
H = {"X-API-Key": API_KEY}


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_auth_required():
    r = client.post("/api/searches", json={"zona": "Nueva Córdoba", "categoria": "gimnasios"})
    assert r.status_code == 401


def test_create_search():
    r = client.post("/api/searches", json={"zona": "Nueva Córdoba", "categoria": "gimnasios"}, headers=H)
    assert r.status_code == 202
    data = r.json()
    assert data["zona"] == "Nueva Córdoba"
    assert data["min_rating"] == 4.3
    assert data["max_days_since_review"] == 90
    assert data["status"] == "pending"
    assert data["poll_url"] == f"/api/searches/{data['id']}"
    assert data["fetch_mode"] == "optimized"
    assert data["include_with_website"] is False
    assert data["target_leads"] is None
    # Acuse de recibo: NO debe traer contadores de resultado (están en el GET)
    assert "total_candidates" not in data
    assert "total_leads" not in data
    assert "calls_cheap" not in data
    # Header Location REST (202)
    assert r.headers.get("location") == f"/api/searches/{data['id']}"
    # check GET
    sid = data["id"]
    r2 = client.get(f"/api/searches/{sid}", headers=H)
    assert r2.status_code == 200
    assert r2.json()["id"] == sid
    # el GET sí trae contadores
    assert "total_candidates" in r2.json()


def test_create_search_target_and_modes():
    r = client.post("/api/searches", json={
        "zona": "BA", "categoria": "tech", "target_leads": 25,
        "fetch_mode": "full", "include_with_website": True,
    }, headers=H)
    assert r.status_code == 202
    data = r.json()
    assert data["target_leads"] == 25
    assert data["fetch_mode"] == "full"
    assert data["include_with_website"] is True


def test_create_search_target_leads_max_50():
    # > 50 -> 422
    r = client.post("/api/searches", json={"zona": "BA", "categoria": "tech", "target_leads": 51}, headers=H)
    assert r.status_code == 422


def test_create_search_fetch_mode_invalid():
    r = client.post("/api/searches", json={"zona": "BA", "categoria": "tech", "fetch_mode": "todo"}, headers=H)
    assert r.status_code == 422


def test_lead_status_validation():
    # create search first to have at least one lead? use fake place
    from app.db import SessionLocal
    from app.models import Search, Lead
    from app.db import init_db

    init_db()
    db = SessionLocal()
    # ensure clean
    s = Search(zona="Z", categoria="C", min_rating=4.3, max_days_since_review=90, status="done")
    db.add(s)
    db.commit()
    db.refresh(s)
    lead = Lead(place_id="test_place_123", search_id=s.id, name="Test Lead", has_website=False, rating=4.5)
    db.add(lead)
    db.commit()
    db.close()

    r = client.post("/api/leads/test_place_123/status", json={"status": "contactado"}, headers=H)
    assert r.status_code == 200
    assert r.json()["status"] == "contactado"

    r2 = client.post("/api/leads/test_place_123/status", json={"status": "invalido"}, headers=H)
    assert r2.status_code == 422


def test_lead_scores_endpoint():
    from app.db import SessionLocal
    from app.models import Search, Lead, LeadScore

    db = SessionLocal()
    s = Search(zona="Z", categoria="C", min_rating=4.3, max_days_since_review=90, status="done")
    db.add(s)
    db.commit()
    db.refresh(s)
    lead = Lead(place_id="scored_place", search_id=s.id, name="Scored", has_website=False, rating=4.5)
    db.add(lead)
    db.flush()
    db.add(LeadScore(
        place_id="scored_place", search_id=s.id, model="openai/gpt-4.1-nano",
        fit_score=88, intent="hot", reason_codes='["no_website","recent_activity"]',
        reasoning="Sin web y actividad reciente",
    ))
    db.commit()
    db.close()

    r = client.get("/api/leads/scored_place/scores", headers=H)
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["fit_score"] == 88
    assert data[0]["intent"] == "hot"
    assert data[0]["reason_codes"] == ["no_website", "recent_activity"]
    assert "Sin web" in (data[0]["reasoning"] or "")

    r404 = client.get("/api/leads/no-existe/scores", headers=H)
    assert r404.status_code == 404
