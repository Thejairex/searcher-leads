import asyncio
from datetime import datetime, timedelta, timezone

from app.db import SessionLocal
from app.models import Search, Lead, ReviewSnapshot, ApiUsage, PlaceCache, LeadScore, WebhookDelivery
from app.tasks import _run_search_async, _score_search_async
from app.config import settings


def _clean():
    db = SessionLocal()
    db.query(WebhookDelivery).delete()
    db.query(LeadScore).delete()
    db.query(ApiUsage).delete()
    db.query(PlaceCache).delete()
    db.query(ReviewSnapshot).delete()
    db.query(Lead).delete()
    db.query(Search).delete()
    db.commit()
    db.close()


def _mk_details(pid, website=None, rating=4.5, days_ago=10):
    now = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "id": f"places/{pid}",
        "displayName": {"text": f"Negocio {pid}"},
        "formattedAddress": "Av Test 123",
        "nationalPhoneNumber": "351 123456",
        "websiteUri": website,
        "rating": rating,
        "userRatingCount": 20,
        "googleMapsUri": f"https://maps.google.com/?cid={pid}",
        "reviews": [{"rating": 5, "text": {"text": "Bueno"}, "publishTime": now.strftime("%Y-%m-%dT%H:%M:%SZ")}],
    }


class FakePlaces:
    def __init__(self, candidates, details_by_id):
        self.candidates = candidates
        self.details = details_by_id
        self.detail_calls = 0

    async def text_search_all(self, zona, categoria, max_pages=3, min_rating=None, lat=None, lng=None, radio=None, included_type=None):
        return self.candidates

    async def get_details(self, pid):
        self.detail_calls += 1
        return self.details[pid]


class CountingPlaces(FakePlaces):
    """Mide la concurrencia máxima de get_details."""
    def __init__(self, candidates, details_by_id):
        super().__init__(candidates, details_by_id)
        self.active = 0
        self.max_active = 0

    async def get_details(self, pid):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.detail_calls += 1
        await asyncio.sleep(0.02)
        try:
            return self.details[pid]
        finally:
            self.active -= 1


def _make_search(db):
    s = Search(zona="Nueva Córdoba", categoria="gimnasios", min_rating=4.3, max_days_since_review=90, status="pending")
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


class FakeScoring:
    def __init__(self, result):
        self.result = result  # (LeadScoreResult|None, model, error)

    async def score_lead(self, lead_data):
        return self.result


def test_scoring_marks_leads_and_status():
    _clean()
    db = SessionLocal()
    from app.scoring_client import LeadScoreResult
    s = _make_search(db)
    sid = s.id
    # 1 lead nuevo para scorer
    lead = Lead(place_id="p_hot", search_id=sid, name="Hot Gym", has_website=False, rating=4.8, status="nuevo")
    db.add(lead)
    db.commit()
    db.close()

    fake = FakeScoring((LeadScoreResult(fit_score=90, intent="hot", reason_codes=["no_website"], reasoning="fit"), "openai/gpt-4.1-nano", None))
    asyncio.run(_score_search_async(sid, scoring_client=fake))

    db = SessionLocal()
    s = db.query(Search).filter(Search.id == sid).first()
    assert s.scored_leads == 1
    assert s.score_errors == 0
    lead = db.query(Lead).filter(Lead.place_id == "p_hot").first()
    assert lead.fit_score == 90
    assert lead.intent == "hot"
    assert lead.score_model == "openai/gpt-4.1-nano"
    assert lead.status == "lista_contacto"  # auto-marcado por intent hot
    # historial en lead_scores
    assert db.query(LeadScore).filter(LeadScore.place_id == "p_hot").count() == 1
    db.close()


def test_scoring_error_does_not_break_others():
    _clean()
    db = SessionLocal()
    from app.scoring_client import LeadScoreResult
    s = _make_search(db)
    sid = s.id
    db.add(Lead(place_id="p_ok", search_id=sid, name="Ok", has_website=False, status="nuevo"))
    db.add(Lead(place_id="p_fail", search_id=sid, name="Fail", has_website=False, status="nuevo"))
    db.commit()
    db.close()

    from app.scoring_client import LeadScoreResult as LSR

    class FailForOne:
        async def score_lead(self, lead_data):
            if lead_data["nombre"] == "Fail":
                return None, "openai/gpt-4.1-nano", "timeout"
            return LSR(fit_score=80, intent="warm", reason_codes=[], reasoning="ok"), "openai/gpt-4.1-nano", None

    asyncio.run(_score_search_async(sid, scoring_client=FailForOne()))

    db = SessionLocal()
    s = db.query(Search).filter(Search.id == sid).first()
    assert s.scored_leads == 1
    assert s.score_errors == 1
    fail = db.query(Lead).filter(Lead.place_id == "p_fail").first()
    assert "timeout" in (fail.score_error or "")
    ok = db.query(Lead).filter(Lead.place_id == "p_ok").first()
    assert ok.fit_score == 80
    db.close()


def test_full_flow_with_filters():
    _clean()
    db = SessionLocal()
    s = _make_search(db)
    sid = s.id
    db.close()

    candidates = [
        {"place_id": "p_web", "has_website": True},   # skip temprano, sin detail
        {"place_id": "p_ok", "has_website": False},    # pasa -> lead
        {"place_id": "p_low", "has_website": False},   # rating bajo -> discard
        {"place_id": "p_old", "has_website": False},   # review vieja -> discard
    ]
    details = {
        "p_ok": _mk_details("p_ok"),
        "p_low": _mk_details("p_low", rating=3.2),
        "p_old": _mk_details("p_old", rating=4.5, days_ago=200),
    }
    fake = FakePlaces(candidates, details)

    asyncio.run(_run_search_async(sid, client=fake))

    db = SessionLocal()
    s = db.query(Search).filter(Search.id == sid).first()
    assert s.status == "done"
    assert s.total_candidates == 4
    assert s.total_leads == 1
    assert s.discarded_has_website == 1
    assert s.discarded_low_rating == 1
    assert s.discarded_no_recent_review == 1
    assert fake.detail_calls == 3  # web no paga detail

    lead = db.query(Lead).filter(Lead.place_id == "p_ok").first()
    assert lead is not None
    assert lead.rating == 4.5
    assert lead.recent_review_detected is True
    assert lead.review_activity_confidence in ("full", "partial", "unknown")
    assert lead.reviews_returned is not None
    assert db.query(ReviewSnapshot).filter(ReviewSnapshot.place_id == "p_ok").count() == 1
    db.close()


def test_cache_reuses_details_on_rerun():
    _clean()
    db = SessionLocal()
    s1 = _make_search(db)
    s1id = s1.id
    s2 = _make_search(db)
    s2id = s2.id
    db.close()

    candidates = [
        {"place_id": "p_ok", "has_website": False},
        {"place_id": "p_low", "has_website": False},
    ]
    details = {
        "p_ok": _mk_details("p_ok"),
        "p_low": _mk_details("p_low", rating=3.2),
    }

    fake1 = FakePlaces(candidates, details)
    asyncio.run(_run_search_async(s1id, client=fake1))
    assert fake1.detail_calls == 2

    # Segunda corrida: los detalles vienen de caché, no se paga de nuevo
    fake2 = FakePlaces(candidates, details)
    asyncio.run(_run_search_async(s2id, client=fake2))
    assert fake2.detail_calls == 0  # reusó cache

    db = SessionLocal()
    s2 = db.query(Search).filter(Search.id == s2id).first()
    assert s2.reused_from_cache == 1  # p_ok reusado; p_low descartado
    assert s2.discarded_low_rating == 1
    db.close()


def test_cache_expires_after_window():
    _clean()
    db = SessionLocal()
    s1 = _make_search(db)
    s1id = s1.id
    s2 = _make_search(db)
    s2id = s2.id
    db.close()

    candidates = [{"place_id": "p_ok", "has_website": False}]
    details = {"p_ok": _mk_details("p_ok")}

    fake1 = FakePlaces(candidates, details)
    asyncio.run(_run_search_async(s1id, client=fake1))

    # Envejecer el caché manualmente
    db = SessionLocal()
    pc = db.query(PlaceCache).filter(PlaceCache.place_id == "p_ok").first()
    pc.fetched_at = datetime.now(timezone.utc) - timedelta(hours=settings.detail_cache_hours + 1)
    db.commit()
    db.close()

    fake2 = FakePlaces(candidates, details)
    asyncio.run(_run_search_async(s2id, client=fake2))
    assert fake2.detail_calls == 1  # caché vencida -> paga de nuevo


def test_concurrent_details_respects_semaphore():
    """Con 8 candidatos y concurrency=2, no debe haber más de 2 get_details simultáneos."""
    _clean()
    db = SessionLocal()
    s = _make_search(db)
    sid = s.id
    db.close()

    old = settings.details_concurrency
    settings.details_concurrency = 2
    try:
        candidates = [{"place_id": f"p{i}", "has_website": False} for i in range(8)]
        details = {f"p{i}": _mk_details(f"p{i}") for i in range(8)}
        fake = CountingPlaces(candidates, details)
        asyncio.run(_run_search_async(sid, client=fake))
        assert fake.detail_calls == 8
        assert fake.max_active <= 2
    finally:
        settings.details_concurrency = old


def test_radio_passthrough_to_client():
    """El worker pasa lat/lng/radio del search al cliente."""
    _clean()
    db = SessionLocal()
    s = Search(zona="BA", categoria="tech", min_rating=4.3, max_days_since_review=90, status="pending", lat=-34.6, lng=-58.38, radio=5000, included_type="software_company")
    db.add(s)
    db.commit()
    db.refresh(s)
    sid = s.id
    db.close()

    class RadioCheckPlaces(FakePlaces):
        def __init__(self):
            super().__init__([], {})
            self.seen = None

        async def text_search_all(self, zona, categoria, max_pages=3, min_rating=None, lat=None, lng=None, radio=None, included_type=None):
            self.seen = (lat, lng, radio, included_type)
            return []

    fake = RadioCheckPlaces()
    asyncio.run(_run_search_async(sid, client=fake))
    assert fake.seen == (-34.6, -58.38, 5000, "software_company")