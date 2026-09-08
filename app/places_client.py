"""
Cliente Places API (New) con estrategia 2 pasos para minimizar costo.

Paso 1 (Text Search Enterprise): fieldMask -> places.id, displayName, websiteUri
Paso 2 (Enterprise+Atmosphere): PlaceDetails -> websiteUri, rating, reviews, ...

Incluye retry con backoff exponencial ante 429/5xx (respeta Retry-After si viene)
y registro de CADA intento en api_usage.
"""
from datetime import datetime, timezone
import asyncio
import time
from typing import Callable
import httpx

from app.config import settings
from app.costing import SKU_TS_ENTERPRISE, SKU_ENTERPRISE

# FieldMasks según investigacion.md
# Paso 1: places.websiteUri pertenece al tier Enterprise (confirmado en sku-details de Google),
# así que el Text Search se factura como Text Search Enterprise (1.000 gratis/mes, $35/1000).
TEXT_SEARCH_FIELD_MASK = "places.id,places.displayName,places.websiteUri"
DETAILS_FIELD_MASK = "id,displayName,formattedAddress,nationalPhoneNumber,websiteUri,rating,userRatingCount,reviews,googleMapsUri"

TEXT_SEARCH_URL = settings.places_text_search_url
DETAILS_URL_TMPL = settings.places_details_url

# Callable: (method, sku, status_code, latency_ms)
UsageCallback = Callable[[str, str, int | None, int | None], None]

RETRYABLE_STATUS = (429, 500, 502, 503)


class PlacesClient:
    def __init__(
        self,
        api_key: str | None = None,
        timeout: float | None = None,
        usage_cb: UsageCallback | None = None,
    ):
        self.api_key = api_key or settings.google_places_api_key
        self.timeout = timeout or settings.request_timeout
        self.usage_cb = usage_cb

    def _record(self, method: str, sku: str, status_code: int | None, latency_ms: int | None):
        if self.usage_cb:
            try:
                self.usage_cb(method, sku, status_code, latency_ms)
            except Exception:
                pass  # nunca debe romper el flujo de scraping

    def _headers(self, field_mask: str) -> dict:
        return {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": field_mask,
        }

    async def _request(
        self,
        method_name: str,
        sku: str,
        http_method: str,
        url: str,
        headers: dict,
        json_body: dict | None = None,
    ) -> httpx.Response:
        """Request con retry (429/5xx). Registra CADA intento en api_usage."""
        for attempt in range(settings.request_max_retries + 1):
            start = time.monotonic()
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.request(http_method, url, headers=headers, json=json_body)
                latency = int((time.monotonic() - start) * 1000)
                self._record(method_name, sku, resp.status_code, latency)
                if resp.status_code in RETRYABLE_STATUS and attempt < settings.request_max_retries:
                    await asyncio.sleep(self._retry_delay(resp, attempt))
                    continue
                resp.raise_for_status()
                return resp
            except httpx.HTTPStatusError as e:
                latency = int((time.monotonic() - start) * 1000)
                self._record(method_name, sku, e.response.status_code, latency)
                if e.response.status_code in RETRYABLE_STATUS and attempt < settings.request_max_retries:
                    await asyncio.sleep(self._retry_delay(e.response, attempt))
                    continue
                raise
            except Exception:
                latency = int((time.monotonic() - start) * 1000)
                self._record(method_name, sku, None, latency)
                raise
        raise RuntimeError(f"{method_name}: reintentos agotados")

    @staticmethod
    def _retry_delay(resp: httpx.Response, attempt: int) -> float:
        """Respeta Retry-After de Google si viene; si no, backoff exponencial."""
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                pass
        return settings.request_retry_base_delay * (2 ** attempt)

    async def text_search(
        self,
        zona: str,
        categoria: str,
        page_token: str | None = None,
        min_rating: float | None = None,
        lat: float | None = None,
        lng: float | None = None,
        radio: int | None = None,
        included_type: str | None = None,
    ) -> dict:
        """
        Paso 1 (Text Search Enterprise). Devuelve id + websiteUri por candidato.
        minRating filtra server-side. Si vienen lat/lng/radio, acota con locationBias.
        includedType restringe al tipo oficial de Google (filtro, no sube el SKU).
        """
        query = f"{categoria} en {zona}".strip()
        body: dict = {"textQuery": query}
        if min_rating is not None:
            body["minRating"] = min_rating
        if page_token:
            body["pageToken"] = page_token
        if lat is not None and lng is not None:
            # locationBias.circle acota por radio (locationRestriction solo acepta rectangle)
            body["locationBias"] = {
                "circle": {
                    "center": {"latitude": lat, "longitude": lng},
                    "radius": radio if radio is not None else 1000,
                }
            }
        if included_type:
            body["includedType"] = included_type

        resp = await self._request(
            "text_search", SKU_TS_ENTERPRISE, "POST", TEXT_SEARCH_URL,
            self._headers(TEXT_SEARCH_FIELD_MASK), json_body=body,
        )
        return resp.json()

    async def text_search_all(
        self,
        zona: str,
        categoria: str,
        max_pages: int = 3,
        min_rating: float | None = None,
        lat: float | None = None,
        lng: float | None = None,
        radio: int | None = None,
        included_type: str | None = None,
    ) -> list[dict]:
        """Itera paginación y retorna candidatos [{"place_id": str, "has_website": bool}]."""
        ids: list[dict] = []
        token: str | None = None
        for _ in range(max_pages):
            data = await self.text_search(
                zona, categoria, page_token=token, min_rating=min_rating,
                lat=lat, lng=lng, radio=radio, included_type=included_type,
            )
            places = data.get("places", [])
            for p in places:
                pid = p.get("id") or p.get("name", "")
                if pid.startswith("places/"):
                    pid = pid.split("/", 1)[1]
                if pid:
                    ids.append({"place_id": pid, "has_website": bool(p.get("websiteUri"))})
            token = data.get("nextPageToken")
            if not token:
                break
        return ids

    async def get_details(self, place_id: str) -> dict:
        """Paso 2: caro (Enterprise+Atmosphere). Trae campos completos."""
        url = DETAILS_URL_TMPL.format(place_id=place_id)
        resp = await self._request(
            "place_details", SKU_ENTERPRISE, "GET", url,
            self._headers(DETAILS_FIELD_MASK),
        )
        return resp.json()

    @staticmethod
    def parse_details(raw: dict) -> dict:
        """Normaliza respuesta Details a dict plano para Lead."""
        pid = raw.get("id", "")
        if pid.startswith("places/"):
            pid = pid.split("/", 1)[1]
        name = raw.get("displayName", {}).get("text") if isinstance(raw.get("displayName"), dict) else raw.get("displayName")
        reviews = raw.get("reviews", []) or []
        # last_review_at = max publishTime de las reviews OBSERVADAS (set parcial).
        # Google devuelve hasta 5 reviews "most relevant", no necesariamente las más nuevas.
        last_review_at: datetime | None = None
        parsed_reviews = []
        for r in reviews:
            pt = r.get("publishTime")
            dt = None
            if pt:
                try:
                    # Google devuelve "2024-12-01T10:00:00Z"
                    if pt.endswith("Z"):
                        pt = pt.replace("Z", "+00:00")
                    dt = datetime.fromisoformat(pt)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                except Exception:
                    dt = None
            if dt and (last_review_at is None or dt > last_review_at):
                last_review_at = dt
            parsed_reviews.append(
                {
                    "author": (r.get("authorAttribution") or {}).get("displayName") or r.get("author"),
                    "rating": r.get("rating"),
                    "text": (r.get("text") or {}).get("text") if isinstance(r.get("text"), dict) else r.get("text"),
                    "publish_time": dt,
                }
            )
        website = raw.get("websiteUri")
        total_reviews = raw.get("userRatingCount")
        reviews_returned = len(parsed_reviews)
        return {
            "place_id": pid or raw.get("name", "").split("/")[-1],
            "name": name or "Sin nombre",
            "address": raw.get("formattedAddress"),
            "phone": raw.get("nationalPhoneNumber"),
            "rating": raw.get("rating"),
            "review_count": total_reviews,
            "reviews_returned": reviews_returned,
            "has_website": bool(website),
            "website_uri": website,
            "maps_uri": raw.get("googleMapsUri"),
            "last_review_at": last_review_at,
            "reviews": parsed_reviews,
        }

    @staticmethod
    def review_confidence(total_reviews: int | None, reviews_returned: int) -> str:
        """Confianza de la observación de actividad.

        - 'full':    el set devuelto cubre todas las reviews (o no hay total).
                     Si no hay review reciente, el negocio probablemente está inactivo.
        - 'partial': Google devolvió un subconjunto; puede haber reviews más nuevas no vistas.
        - 'unknown': no se conoce el total de reviews (userRatingCount ausente).
        """
        if total_reviews is None:
            return "unknown"
        if total_reviews <= reviews_returned:
            return "full"
        return "partial"

    @staticmethod
    def filter_reason(parsed: dict, min_rating: float, max_days_since_review: int) -> str | None:
        """Retorna motivo de descarte o None si el lead pasa.

        Motivos: 'has_website' | 'low_rating' | 'no_recent_review'.
        'no_recent_review' = no se detectó review reciente en el set devuelto por la API
        (no afirma que el negocio esté inactivo: el set puede ser parcial).
        """
        if parsed.get("has_website"):
            return "has_website"
        rating = parsed.get("rating")
        if rating is not None and rating < min_rating:
            return "low_rating"
        last = parsed.get("last_review_at")
        if last is None:
            return "no_recent_review"
        now = datetime.now(timezone.utc)
        if (now - last).days > max_days_since_review:
            return "no_recent_review"
        return None

    @staticmethod
    def passes_filters(parsed: dict, min_rating: float, max_days_since_review: int) -> bool:
        """Backward-compat: True si el lead pasa todos los filtros."""
        return PlacesClient.filter_reason(parsed, min_rating, max_days_since_review) is None
