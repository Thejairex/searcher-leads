from datetime import datetime, timezone, timedelta
import asyncio
import httpx
from app.places_client import PlacesClient, TEXT_SEARCH_FIELD_MASK, DETAILS_FIELD_MASK


class FakeResponse:
    def __init__(self, status_code, json_data=None, headers=None):
        self.status_code = status_code
        self._json = json_data or {}
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            req = httpx.Request("GET", "http://test")
            raise httpx.HTTPStatusError(f"HTTP {self.status_code}", request=req, response=self)

    def json(self):
        return self._json


class FakeAsyncClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def request(self, method, url, headers=None, json=None):
        self.calls += 1
        if not self.responses:
            raise RuntimeError("no more responses")
        return self.responses.pop(0)


async def _request_with_retry_test(client, responses, max_retries=3, base_delay=0.0):
    import app.places_client as pc
    pc.settings.request_max_retries = max_retries
    pc.settings.request_retry_base_delay = base_delay
    fake = FakeAsyncClient(responses)
    original = httpx.AsyncClient
    httpx.AsyncClient = lambda *a, **k: fake
    try:
        resp = await client._request("text_search", "ts_enterprise", "POST", "http://test", {}, None)
        return resp, fake
    finally:
        httpx.AsyncClient = original


def test_field_masks():
    assert "places.id" in TEXT_SEARCH_FIELD_MASK
    # Enterprise: websiteUri en TextSearch para filtrar con-web sin pagar el detail caro
    # (mapeo oficial: websiteUri activa Text Search Enterprise)
    assert "places.websiteUri" in TEXT_SEARCH_FIELD_MASK
    assert "websiteUri" in DETAILS_FIELD_MASK
    assert "reviews" in DETAILS_FIELD_MASK


def test_retry_success_after_429():
    client = PlacesClient(api_key="test")
    resp, fake = asyncio.run(
        _request_with_retry_test(
            client,
            [FakeResponse(429, headers={"Retry-After": "0"}), FakeResponse(200, {"ok": True})],
            max_retries=3,
            base_delay=0.0,
        )
    )
    assert fake.calls == 2
    assert resp.json() == {"ok": True}


def test_retry_exhausted_raises():
    client = PlacesClient(api_key="test")
    import pytest
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(
            _request_with_retry_test(
                client,
                [FakeResponse(429), FakeResponse(429), FakeResponse(429), FakeResponse(429)],
                max_retries=3,
                base_delay=0.0,
            )
        )


def test_no_retry_on_200():
    client = PlacesClient(api_key="test")
    resp, fake = asyncio.run(
        _request_with_retry_test(client, [FakeResponse(200, {"ok": 1})], max_retries=3, base_delay=0.0)
    )
    assert fake.calls == 1


def test_text_search_radio_builds_location_restriction():
    captured = {}

    class CaptureClient(FakeAsyncClient):
        def __init__(self):
            super().__init__([FakeResponse(200, {"places": []})])

        async def request(self, method, url, headers=None, json=None):
            captured["json"] = json
            return await super().request(method, url, headers=headers, json=json)

    import app.places_client as pc
    pc.settings.request_max_retries = 0
    fake = CaptureClient()
    original = httpx.AsyncClient
    httpx.AsyncClient = lambda *a, **k: fake
    try:
        asyncio.run(PlacesClient(api_key="test").text_search("BA", "tech", lat=-34.6, lng=-58.38, radio=5000))
    finally:
        httpx.AsyncClient = original

    assert captured["json"]["locationBias"] == {
        "circle": {
            "center": {"latitude": -34.6, "longitude": -58.38},
            "radius": 5000,
        }
    }
    # sin lat/lng no debe ir locationBias
    captured.clear()
    fake2 = CaptureClient()
    httpx.AsyncClient = lambda *a, **k: fake2
    try:
        asyncio.run(PlacesClient(api_key="test").text_search("BA", "tech"))
    finally:
        httpx.AsyncClient = original
    assert "locationBias" not in captured["json"]


def test_text_search_included_type_in_body():
    captured = {}

    class CaptureClient(FakeAsyncClient):
        def __init__(self):
            super().__init__([FakeResponse(200, {"places": []})])

        async def request(self, method, url, headers=None, json=None):
            captured["json"] = json
            return await super().request(method, url, headers=headers, json=json)

    import app.places_client as pc
    pc.settings.request_max_retries = 0
    fake = CaptureClient()
    original = httpx.AsyncClient
    httpx.AsyncClient = lambda *a, **k: fake
    try:
        asyncio.run(PlacesClient(api_key="test").text_search("BA", "tech", included_type="software_company"))
    finally:
        httpx.AsyncClient = original
    assert captured["json"]["includedType"] == "software_company"

    # sin included_type no va el campo
    captured.clear()
    fake2 = CaptureClient()
    httpx.AsyncClient = lambda *a, **k: fake2
    try:
        asyncio.run(PlacesClient(api_key="test").text_search("BA", "tech"))
    finally:
        httpx.AsyncClient = original
    assert "includedType" not in captured["json"]


def test_parse_details():
    raw = {
        "id": "places/ChIJ123",
        "displayName": {"text": "Gimnasio Test"},
        "formattedAddress": "Av Test 123",
        "nationalPhoneNumber": "351 123456",
        "websiteUri": None,
        "rating": 4.8,
        "userRatingCount": 42,
        "googleMapsUri": "https://maps.google.com/?cid=1",
        "reviews": [
            {"rating": 5, "text": {"text": "Excelente"}, "publishTime": "2026-04-01T10:00:00Z"},
        ],
    }
    p = PlacesClient.parse_details(raw)
    assert p["place_id"] == "ChIJ123"
    assert p["has_website"] is False
    assert p["last_review_at"] is not None
    assert p["review_count"] == 42
    assert p["reviews_returned"] == 1


def test_review_confidence():
    # total > devueltas -> parcial (set incompleto, puede haber reviews más nuevas)
    assert PlacesClient.review_confidence(42, 5) == "partial"
    # total <= devueltas -> completo (todas observadas o set cubre todo)
    assert PlacesClient.review_confidence(5, 5) == "full"
    assert PlacesClient.review_confidence(3, 5) == "full"
    # sin total -> unknown
    assert PlacesClient.review_confidence(None, 5) == "unknown"


def test_filter_reason_ok():
    parsed = {
        "has_website": False,
        "rating": 4.5,
        "last_review_at": datetime.now(timezone.utc) - timedelta(days=10),
    }
    assert PlacesClient.filter_reason(parsed, 4.3, 90) is None


def test_filter_reason_website():
    parsed = {"has_website": True, "rating": 5.0, "last_review_at": datetime.now(timezone.utc)}
    assert PlacesClient.filter_reason(parsed, 4.3, 90) == "has_website"


def test_filter_reason_low_rating():
    parsed = {"has_website": False, "rating": 3.5, "last_review_at": datetime.now(timezone.utc)}
    assert PlacesClient.filter_reason(parsed, 4.3, 90) == "low_rating"


def test_filter_reason_old_review():
    parsed = {"has_website": False, "rating": 5.0, "last_review_at": datetime.now(timezone.utc) - timedelta(days=200)}
    assert PlacesClient.filter_reason(parsed, 4.3, 90) == "no_recent_review"


def test_filter_reason_no_review():
    parsed = {"has_website": False, "rating": 5.0, "last_review_at": None}
    assert PlacesClient.filter_reason(parsed, 4.3, 90) == "no_recent_review"


def test_passes_filters_backcompat():
    parsed = {
        "has_website": False,
        "rating": 4.5,
        "last_review_at": datetime.now(timezone.utc) - timedelta(days=10),
    }
    assert PlacesClient.passes_filters(parsed, 4.3, 90) is True