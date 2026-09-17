"""
Cliente de lead scoring vía OpenRouter (investigacion.md:198-208).

- Rúbrica ICP explícita en el prompt (investigacion.md:138-143).
- Salida JSON estructurada (no opinión libre), validada con Pydantic.
- Regla anti-alucinación: no inferir datos faltantes, asignar 0.
- Fallback automático a un segundo modelo si el primario falla.
"""
import json
from datetime import datetime
import httpx
from pydantic import BaseModel, field_validator

from app.config import settings

ALLOWED_INTENTS = {"hot", "warm", "cold"}
ALLOWED_REASON_CODES = {
    "no_website", "high_rating", "recent_activity", "industry_fit",
    "has_website", "low_review_volume", "no_recent_activity", "low_rating",
}

SYSTEM_PROMPT = """Sos un evaluador de leads B2B. Puntás negocios según una rúbrica fija (Ideal Customer Profile).

Rúbrica (máx 100 puntos):
- Sin sitio web: +30 (0 si tiene sitio web)
- Rating alto con volumen real de reseñas (no solo 2 reseñas de 5 estrellas): +25
- Actividad reciente en reseñas (negocio operativo): +25
- Rubro que calza con lo que vende la empresa: +20

Regla de techo:
- Si el negocio TIENE sitio web, su fit_score máximo es 84. Nunca puede ser "hot", a lo sumo "warm".
  Usá reason_code "has_website" en ese caso.
- Solo un negocio SIN sitio web y con buen puntaje puede llegar a "hot".

Rangos de decisión:
- fit_score 85 o más = "hot" (contactar ya) — reservado para SIN sitio web
- fit_score 60 a 84 = "warm"
- fit_score menor a 60 = "cold"

Reglas:
- NO infieras datos faltantes. Si falta un dato (por ejemplo, no hay reseñas recientes),
  asigná 0 al ítem correspondiente y usá el reason_code adecuado.
- Devolvé SOLO un objeto JSON con este formato exacto:
{"fit_score": 72, "intent": "warm", "reason_codes": ["has_website", "high_rating"], "reasoning": "frase corta justificando el score"}
- reason_codes válidos: no_website, high_rating, recent_activity, industry_fit,
  has_website, low_review_volume, no_recent_activity, low_rating
- Si tiene sitio web, incluí obligatoriamente "has_website" en reason_codes.
- No agregues texto fuera del JSON ni markdown."""


def build_user_prompt(lead_data: dict) -> str:
    """Solo mandamos los campos que pesan en la rúbrica (regla investigacion.md:166). Sin tel/address."""
    lines = [
        f"Negocio: {lead_data.get('nombre', '?')}",
        f"Categoría: {lead_data.get('categoria', '?')}",
        f"Zona: {lead_data.get('zona', '?')}",
        f"Tiene sitio web: {'sí' if lead_data.get('has_website') else 'no'}",
        f"Rating: {lead_data.get('rating')}",
        f"Cantidad de reseñas: {lead_data.get('review_count')}",
    ]
    last = lead_data.get("last_review_at")
    if isinstance(last, datetime):
        last = last.isoformat()
    lines.append(f"Última reseña observada: {last if last else 'no hay datos'}")
    texts = lead_data.get("review_texts") or []
    if texts:
        quoted = " | ".join(f'"{t[:200]}"' for t in texts)
        lines.append(f"Reseñas: {quoted}")
    return "\n".join(lines)


class LeadScoreResult(BaseModel):
    fit_score: int
    intent: str
    reason_codes: list[str] = []
    reasoning: str = ""

    @field_validator("fit_score")
    @classmethod
    def _clamp_score(cls, v):
        return max(0, min(100, v))

    @field_validator("intent")
    @classmethod
    def _clamp_intent(cls, v, info):
        from pydantic import ValidationInfo
        score = info.data.get("fit_score")
        if v in ALLOWED_INTENTS:
            return v
        if score is None or score >= 85:
            return "hot"
        if score >= 60:
            return "warm"
        return "cold"

    def sorted_reason_codes(self) -> list[str]:
        codes = [c for c in self.reason_codes if c in ALLOWED_REASON_CODES]
        # evitar duplicados preservando orden
        return list(dict.fromkeys(codes))


def parse_and_clamp(raw: dict) -> LeadScoreResult:
    return LeadScoreResult.model_validate(raw)


class ScoringClient:
    """Inyectable (fetch) para tests; usa httpx por defecto. Sin dependencias nuevas."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        fallback_model: str | None = None,
        fetch=None,
        timeout: float = 30.0,
    ):
        self.api_key = api_key if api_key is not None else settings.openrouter_api_key
        self.model = model or settings.score_model
        self.fallback_model = fallback_model or settings.score_fallback_model
        self.timeout = timeout
        self.fetch = fetch or self._default_fetch

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _body(self, model: str, user_prompt: str) -> dict:
        return {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": settings.score_temperature,
            "response_format": {"type": "json_object"},
        }

    async def _default_fetch(self, model: str, headers: dict, body: dict) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(settings.openrouter_url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)

    @staticmethod
    def _enforce_ceiling(result: LeadScoreResult, has_website: bool) -> LeadScoreResult:
        """Opción 2 con techo: con sitio web nunca puede ser hot, máximo 84 (warm)."""
        if not has_website:
            return result
        codes = list(result.reason_codes)
        if "has_website" not in codes:
            codes = ["has_website"] + codes
        result.reason_codes = codes
        if result.intent == "hot" or result.fit_score >= 85:
            result.intent = "warm"
            result.fit_score = min(result.fit_score, 84)
        return result

    async def score_lead(self, lead_data: dict) -> tuple[LeadScoreResult | None, str, str | None]:
        """Retorna (result, model_usado, error). Error solo si AMBOS modelos fallan."""
        if not self.api_key:
            return None, self.model, "OPENROUTER_API_KEY no configurada"
        user_prompt = build_user_prompt(lead_data)
        has_website = bool(lead_data.get("has_website"))
        last_err: Exception | None = None
        for model in (self.model, self.fallback_model):
            try:
                raw = await self.fetch(model, self._headers(), self._body(model, user_prompt))
                result = parse_and_clamp(raw)
                result = self._enforce_ceiling(result, has_website)
                return result, model, None
            except Exception as e:  # noqa: BLE001 - fallback cubre errores de parse y red
                last_err = e
        return None, self.model, str(last_err)