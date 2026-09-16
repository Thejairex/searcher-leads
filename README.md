# search-leads

Servicio que envuelve Google Places API (New) para extraer leads cualificados.

## Estrategia 2 pasos (ver `investigacion.md`)

1. **Barato (Text Search Enterprise):** `TextSearch` con `FieldMask: places.id,places.displayName,places.websiteUri` + `minRating` server-side -> candidatos con flag `has_website` (una consulta trae 20-60 candidatos)
2. **Caro (Enterprise+Atmosphere):** `PlaceDetails` solo para candidatos sin web, con `FieldMask: websiteUri,rating,reviews,...` + filtros en código (`sin web`, `rating>=4.3`, `actividad <90d`)

### Optimizaciones de costo

- **Skip temprano:** candidatos con `websiteUri` (del Text Search Enterprise) se descartan sin pagar el detail caro.
- **`minRating` en el body del TextSearch:** Google filtra rating bajo server-side, sin subir el SKU.
- **Caché de detalles** (`place_cache`, 7 días): re-correr una zona no re-paga `PlaceDetails` de place_ids ya vistos.

Cada `search` expone contadores para auditar dónde se filtra la plata:

| Campo | Qué mide |
|---|---|
| `calls_cheap` / `calls_expensive` | TextSearch (Enterprise) vs PlaceDetails (Ent+Atmos) |
| `discarded_has_website` | descartados por web, sin pagar detail |
| `discarded_low_rating` | descartados por rating |
| `discarded_no_recent_review` | descartados por no observar review reciente (<90d) en el set devuelto |
| `reused_from_cache` | detalles reusados de caché, sin pagar |
| `est_cost_usd` | costo estimado según cupos (`investigacion.md:83,115`) |

## Stack

FastAPI + SQLite + BackgroundTasks (sin Postgres/Redis para v1 simple). Celery/Redis opcional para escalar.

## Nota sobre reviews (semántica)

La Places API devuelve **hasta 5 reviews "most relevant"**, no necesariamente las más nuevas. Por eso los leads no se presentan con "actividad reciente" como certeza, sino como **observación parcial**:

- `recent_review_detected` — ¿la API devolvió al menos una review dentro de la ventana (90d)?
- `review_activity_confidence` — `full` (el set cubre todas las reviews) / `partial` (hay más reviews que las devueltas, puede haber actividad no vista) / `unknown`.
- `reviews_returned` — cuántas reviews trajo la API en la última corrida.

```json
{
  "recent_review_detected": true,
  "last_review_at": "2026-07-31T18:43:37Z",
  "review_activity_confidence": "partial"
}
```

## Lead scoring (OpenRouter)

Después de cada búsqueda, los leads pasan por un **scorer LLM** zero-shot (rúbrica ICP de `investigacion.md:138`): sin web +30, rating-con-volumen +25, actividad reciente +25, rubro fit +20. 85+ = `hot`, 60–84 = `warm`, <60 = `cold`.

- Modelo primario `openai/gpt-4.1-nano`, fallback `google/gemini-2.0-flash-lite` (~$0.012 por 200 leads).
- Salida **JSON estructurado** validado con Pydantic; el modelo no infiere datos faltantes.
- Resultado en `GET /leads` (`fit_score`, `intent`, `score_model`, `scored_at`) + historial en `lead_scores`.
- `intent=hot` auto-marca el lead `lista_contacto` (si está `nuevo`). Filtrá con `?intent=hot` o `?min_fit_score=`.
- Re-scoring manual: `POST /api/searches/{id}/score`.

Requiere `OPENROUTER_API_KEY` en `.env`. Si falta o `SCORE_ENABLED=false`, la búsqueda igual corre, solo sin scoring.

## Config

`.env` soporta dos formatos:
```
GOOGLE_PLACES_API_KEY=AIza...
API_KEY=dev-key-123
OPENROUTER_API_KEY=sk-or-...
```
o archivo con solo la key cruda `AIza...` (autodetectado).

## Run

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8001
```

## Endpoints

- `POST /api/searches` (202, async) — dispara búsqueda. Body: `{zona, categoria, lat?, lng?, radio?, target_leads?, fetch_mode?, include_with_website?, included_type?, min_rating?, max_days_since_review?}`. Devuelve **acuse de recibo** (`id`, `status: pending`, `poll_url`) + header `Location`.

> **Async**: el `POST` responde `202` antes de que el worker termine. El body es solo el job aceptado — no trae resultados. Usá `poll_url` (o el header `Location`) y hacé polling con `GET /api/searches/{id}` hasta que `status: done`.
- `GET /api/searches/{id}` — estado + contadores de uso (`calls_cheap`, `calls_expensive`, `est_cost_usd`) + `scored_leads`
- `GET /api/searches/{id}/leads?has_website=false&min_rating=4.3&intent=hot&min_fit_score=85` — lista filtrada
- `GET /api/searches/{id}/candidates?page=1&limit=20` — candidatos crudos de la búsqueda (barato, DB)
- `GET /api/candidates/{place_id}` — detalle caro desacoplado (Enterprise+Atmosphere); `?promote=true&search_id={id}` promueve a lead si pasa filtros
- `GET /api/leads/{place_id}` — detalle + reviews + score LLM
- `GET /api/leads/{place_id}/scores` — historial de scores LLM (reasoning + reason_codes)
- `POST /api/leads/{place_id}/status` — mini-CRM (nuevo/lista_contacto/contactado/descartado/convertido)
- `POST /api/searches/{id}/score` — re-scoring LLM de la corrida
- `GET /api/searches/{id}/export.csv` — export CSV de los leads de la corrida (UTF-8 BOM)
- `GET /api/leads/export.csv?intent=hot&status=lista_contacto` — export CSV de todos los leads con filtros
- `GET /api/usage?month=2026-09` — totales mensuales por SKU vs cupo gratis + costo estimado
- `GET /api/searches/{id}/usage` — desglose de llamadas de una corrida

Auth: `X-API-Key: dev-key-123` (configurable via `API_KEY`)

## Monitoreo de costos

Cada llamada a Google se loguea en `api_usage` (método, SKU, status, latencia, `month_key`). Los contadores se graban en cada `search` (`investigacion.md:83,115-118`):

| SKU | Llamada | Cupo gratis/mes | Exceso /1000 |
|---|---|---|---|
| Text Search Enterprise (barato) | Text Search `places.id,places.displayName,places.websiteUri` | 1.000 | $35 |
| Enterprise+Atmosphere (caro) | Place Details `websiteUri,rating,reviews` | 1.000 | $25 |

```powershell
.\scripts\usage.ps1                    # resumen del mes actual
.\scripts\usage.ps1 -Month 2026-09     # mes puntual
```

## Env vars

`DATABASE_URL=sqlite:///./data/search_leads.db` (default), `DEFAULT_MIN_RATING=4.3`, `DEFAULT_MAX_DAYS_SINCE_REVIEW=90`, `DETAIL_CACHE_HOURS=168`, `FREE_TS_ENTERPRISE_MONTHLY`, `FREE_ENTERPRISE_MONTHLY`, `COST_TS_ENTERPRISE_PER_1000`, `COST_ENTERPRISE_PER_1000`, `MAX_CANDIDATES`, `DEFAULT_TARGET_LEADS`, `OPENROUTER_API_KEY`, `SCORE_ENABLED`, `SCORE_MODEL`, `SCORE_FALLBACK_MODEL`, `SCORE_TEMPERATURE`, `USE_INCLUDED_TYPE`, `WEBHOOK_URL`, `WEBHOOK_ENABLED`, `WEBHOOK_TIMEOUT`
