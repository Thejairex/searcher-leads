import asyncio
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from app.main import app
from app.config import settings
from app.db import SessionLocal
from app.models import Search, Lead, ReviewSnapshot, WebhookDelivery
from app.webhooks import deliver_webhook

client = TestClient(app)
H = {"X-API-Key": settings.api_key}


def _setup_search_with_lead():
    db = SessionLocal()
    s = Search(zona="BA", categoria="gimnasios", min_rating=4.3, max_days_since_review=90, status="done", total_leads=1)
    db.add(s)
    db.commit()
    db.refresh(s)
    lead = Lead(
        place_id="ChIJtest", search_id=s.id, name="Gym Águila", address="Av Test 123",
        phone="351 1234", rating=4.8, review_count=30, has_website=False, last_review_at=datetime.now(timezone.utc),
        fit_score=85, intent="hot", status="lista_contacto",
    )
    db.add(lead)
    db.flush()
    db.add(ReviewSnapshot(place_id="ChIJtest", author="Juan", rating=5, text="Muy bueno, recomendado", publish_time=datetime.now(timezone.utc)))
    db.commit()
    sid = s.id
    db.close()
    return sid


def test_export_csv():
    from app.models import Search, Lead, ReviewSnapshot
    from app.db import SessionLocal
    # limpiar previo
    db = SessionLocal()
    db.query(ReviewSnapshot).delete()
    db.query(Lead).delete()
    db.query(Search).delete()
    db.commit()
    db.close()

    sid = _setup_search_with_lead()
    r = client.get(f"/api/searches/{sid}/export.csv", headers=H)
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]
    text = r.content.decode("utf-8-sig")  # BOM
    lines = text.strip().splitlines()
    # header + 1 fila
    assert len(lines) == 2
    assert "place_id" in lines[0]
    assert "Gym" in lines[1]
    assert "Muy bueno" in lines[1]  # reviews incluidas


def test_export_csv_404():
    r = client.get("/api/searches/no-existe/export.csv", headers=H)
    assert r.status_code == 404


class FakePost:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        self.calls += 1
        if not self.responses:
            raise RuntimeError("no more")
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class FakeResp:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


async def _run_webhook(search_id: str, fake, url="http://webhook.test"):
    import app.webhooks as wh
    old_url, old_enabled, old_client = wh.settings.webhook_url, wh.settings.webhook_enabled, wh.httpx.AsyncClient
    wh.settings.webhook_url = url
    wh.settings.webhook_enabled = True
    wh.httpx.AsyncClient = lambda *a, **k: fake
    try:
        await deliver_webhook(search_id)
    finally:
        wh.settings.webhook_url, wh.settings.webhook_enabled, wh.httpx.AsyncClient = old_url, old_enabled, old_client


def test_webhook_success_records_delivery():
    from app.models import Search
    from app.db import SessionLocal
    db = SessionLocal()
    db.query(WebhookDelivery).delete()
    db.query(Search).delete()
    db.commit()
    s = Search(id="wh-test-1", zona="BA", categoria="tech", status="done", total_leads=2)
    db.add(s)
    db.commit()
    db.close()

    fake = FakePost([FakeResp(200, "ok")])
    asyncio.run(_run_webhook("wh-test-1", fake))
    assert fake.calls == 1

    db = SessionLocal()
    d = db.query(WebhookDelivery).filter(WebhookDelivery.search_id == "wh-test-1").first()
    assert d is not None
    assert d.success is True
    assert d.status_code == 200
    db.close()


def test_webhook_failure_does_not_break_and_records():
    from app.models import Search
    from app.db import SessionLocal
    db = SessionLocal()
    db.query(WebhookDelivery).delete()
    db.query(Search).delete()
    db.commit()
    s = Search(id="wh-test-2", zona="BA", categoria="tech", status="failed", total_leads=0)
    db.add(s)
    db.commit()
    db.close()

    fake = FakePost([RuntimeError("connection refused")])
    asyncio.run(_run_webhook("wh-test-2", fake))
    assert fake.calls == 1

    db = SessionLocal()
    d = db.query(WebhookDelivery).filter(WebhookDelivery.search_id == "wh-test-2").first()
    assert d is not None
    assert d.success is False
    assert "connection refused" in (d.response_body or "")
    db.close()


def test_webhook_disabled_no_call():
    import app.webhooks as wh
    from app.models import Search
    from app.db import SessionLocal
    db = SessionLocal()
    db.query(WebhookDelivery).delete()
    db.query(Search).delete()
    db.commit()
    s = Search(id="wh-test-3", zona="BA", categoria="tech", status="done")
    db.add(s)
    db.commit()
    db.close()

    fake = FakePost([])
    old_url, old_enabled = wh.settings.webhook_url, wh.settings.webhook_enabled
    wh.settings.webhook_url = "http://webhook.test"
    wh.settings.webhook_enabled = False
    try:
        asyncio.run(deliver_webhook(s))
    finally:
        wh.settings.webhook_url, wh.settings.webhook_enabled = old_url, old_enabled
    assert fake.calls == 0