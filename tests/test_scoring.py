import json
import pytest

from app.scoring_client import (
    ScoringClient,
    LeadScoreResult,
    parse_and_clamp,
    build_user_prompt,
    SYSTEM_PROMPT,
)


def ok_content(raw: dict) -> str:
    return json.dumps(raw)


class FakeFetch:
    """Simula la respuesta HTTP de OpenRouter. Devolver contenido por modelo."""
    def __init__(self, contents: dict):
        self.contents = contents  # model -> raw dict (o excepción)

    async def __call__(self, model, headers, body):
        val = self.contents.get(model)
        if isinstance(val, Exception):
            raise val
        if val is None:
            raise RuntimeError("modelo no soportado")
        return val


def _make_client(fetch, api_key="sk-or-test"):
    return ScoringClient(api_key=api_key, fetch=fetch, model="openai/gpt-4.1-nano", fallback_model="google/gemini-2.0-flash-lite")


# --- tests unitarios de prompt/parse ---


def test_build_user_prompt_no_sensitive_fields():
    lead_data = {
        "nombre": "Gym Test",
        "categoria": "gimnasios",
        "zona": "Nueva Córdoba",
        "has_website": False,
        "rating": 4.8,
        "review_count": 30,
        "last_review_at": None,
        "review_texts": ["Muy buen lugar"],
    }
    p = build_user_prompt(lead_data)
    assert "Gym Test" in p
    assert "Nueva Córdoba" in p
    assert "sí" in p or "no" in p
    # no debe incluir tel/address
    assert "phone" not in p
    assert "address" not in p


def test_parse_and_clamp_valid():
    raw = {"fit_score": 82, "intent": "hot", "reason_codes": ["no_website", "recent_activity"], "reasoning": "ok"}
    r = parse_and_clamp(raw)
    assert r.fit_score == 82
    assert r.intent == "hot"
    assert r.sorted_reason_codes() == ["no_website", "recent_activity"]


def test_parse_and_clamp_out_of_range():
    raw = {"fit_score": 150, "intent": "warm", "reason_codes": [], "reasoning": ""}
    r = parse_and_clamp(raw)
    assert r.fit_score == 100


def test_parse_and_clamp_intent_fallback_by_score():
    # intent inválido -> se deriva del score
    r = parse_and_clamp({"fit_score": 90, "intent": "URGENTE", "reason_codes": [], "reasoning": ""})
    assert r.intent == "hot"
    r2 = parse_and_clamp({"fit_score": 70, "intent": "", "reason_codes": [], "reasoning": ""})
    assert r2.intent == "warm"


def test_reason_codes_filtered():
    raw = {"fit_score": 80, "intent": "warm", "reason_codes": ["no_website", "INVALIDO", "no_website"], "reasoning": ""}
    r = parse_and_clamp(raw)
    assert r.sorted_reason_codes() == ["no_website"]


# --- tests async del cliente ---


@pytest.mark.asyncio
async def test_score_lead_success():
    fetch = FakeFetch({"openai/gpt-4.1-nano": {"fit_score": 88, "intent": "hot", "reason_codes": ["no_website"], "reasoning": "buen fit"}})
    client = _make_client(fetch)
    result, model, err = await client.score_lead({"nombre": "X"})
    assert err is None
    assert model == "openai/gpt-4.1-nano"
    assert result.fit_score == 88
    assert result.intent == "hot"


@pytest.mark.asyncio
async def test_score_lead_fallback():
    fetch = FakeFetch({
        "openai/gpt-4.1-nano": Exception("boom primario"),
        "google/gemini-2.0-flash-lite": {"fit_score": 70, "intent": "warm", "reason_codes": [], "reasoning": "ok"},
    })
    client = _make_client(fetch)
    result, model, err = await client.score_lead({"nombre": "X"})
    assert err is None
    assert model == "google/gemini-2.0-flash-lite"
    assert result.fit_score == 70


@pytest.mark.asyncio
async def test_score_lead_total_failure():
    fetch = FakeFetch({
        "openai/gpt-4.1-nano": Exception("a"),
        "google/gemini-2.0-flash-lite": Exception("b"),
    })
    client = _make_client(fetch)
    result, model, err = await client.score_lead({"nombre": "X"})
    assert result is None
    assert err is not None


@pytest.mark.asyncio
async def test_score_lead_no_api_key():
    client = ScoringClient(api_key="", fetch=FakeFetch({}))
    result, model, err = await client.score_lead({"nombre": "X"})
    assert result is None
    assert "OPENROUTER_API_KEY" in err


def test_system_prompt_has_rubric_and_json_schema():
    # rúbrica presente (investigacion.md:138-143)
    for criterion in ("Sin sitio web", "+30", "+25", "+20"):
        assert criterion in SYSTEM_PROMPT
    # JSON estructurado forzado (investigacion.md:149-162)
    assert "fit_score" in SYSTEM_PROMPT
    assert "intent" in SYSTEM_PROMPT
    # regla anti-invención (investigacion.md:167)
    assert "NO infieras" in SYSTEM_PROMPT