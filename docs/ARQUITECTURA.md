# Search Leads — Explicación del código y cómo funciona

Servicio FastAPI que envuelve la **Places API (New)** de Google para extraer leads cualificados (negocios sin sitio web, con buen rating y con al menos una review reciente observada), exponiéndolos detrás de una API interna con autenticación por API key. Incluye contabilidad de uso/costo contra los cupos gratuitos de Google.

---

## 1. Idea general

```
 Cliente interno ──X-API-Key──> FastAPI ──BackgroundTasks──> Worker async
                                                                 │
                                                     Places API (New) de Google
                                                                 │
                                                              SQLite
                                                        (searches, leads,
                                                      reviews, usage, cache)
```

`POST /api/searches` **no bloquea**: responde `202` con un **acuse de recibo** (`search_id`, `status: pending`, `poll_url`) y dispara el procesamiento en background (`app/tasks.py:188`). El body del POST **no trae resultados** — los contadores solo existen tras el worker. El cliente hace polling con `GET /api/searches/{id}` (usando `poll_url` o el header `Location`) hasta que el estado pasa a `done`.

La decisión central de diseño es la **estrategia de 2 pasos** (documentada en `investigacion.md`) para minimizar el costo de Google:

1. **Paso 1 — barato (Text Search Enterprise):** busca candidatos por zona + categoría, pidiendo `id`, `displayName` y `websiteUri`. Es Enterprise (1.000/mes gratis) porque `websiteUri` es un campo Enterprise, pero **una sola consulta trae 20-60 candidatos**, así que el costo por candidato (~$0.0017) es mucho menor que el Detail caro ($0.025).
2. **Paso 2 — caro (Place Details, SKU Enterprise+Atmosphere):** solo para los candidatos que sobreviven al filtro barato, pide el detalle completo (web, rating, reviews, teléfono).

El **80% de las llamadas caras se ahorran** descartando candidatos con sitio web (del Paso 1) antes de pagar el detail.

---

## 2. Estructura de archivos

| Archivo | Responsabilidad |
|---|---|
| `app/config.py` | Configuración vía `.env` (API key de Google, DB, cupos, precios, caché) |
| `app/db.py` | Conexión SQLite + migración automática de columnas |
| `app/models.py` | Modelos ORM: `Search`, `Lead`, `ReviewSnapshot`, `ApiUsage`, `PlaceCache`, `ApiClient` |
| `app/schemas.py` | Schemas Pydantic de entrada/salida de la API |
| `app/places_client.py` | Cliente HTTP de Places API (2 pasos) + parsing + filtros |
| `app/costing.py` | Cálculo de costo y cupos mensuales por SKU |
| `app/tasks.py` | Worker async: orquesta corrida, filtra, persiste, mide |
| `app/main.py` | Endpoints FastAPI + auth `X-API-Key` |
| `app/security.py` | Verificación de API key |
| `tests/` | Tests unitarios (flujo, filtros, caché, costo, API) |
| `scripts/usage.ps1` | Monitoreo de consumo/costo desde consola |
| `data/search_leads.db` | Base SQLite generada en runtime |

---

## 3. Modelo de datos (`app/models.py`)

### `searches`
Una corrida de búsqueda. Guarda zona/categoría, filtros usados, estado y **contadores de diagnóstico**:

- `status`: `pending → running → done | failed`
- `total_candidates` / `total_leads`: conversión del embudo
- `calls_cheap` / `calls_expensive`: llamadas Text Search (Enterprise) vs Place Details (Enterprise+Atmosphere)
- `discarded_has_website` / `discarded_low_rating` / `discarded_no_recent_review`: dónde se descartan los candidatos (auditoría del gasto)
- `reused_from_cache`: cuántos detalles salieron de caché sin pagar
- `est_cost_usd`: costo estimado según cupos del mes

### `leads`
Un negocio cualificado. Clave primaria `place_id` (dedupe natural). Contiene nombre, dirección, teléfono, rating, cantidad de reviews, `has_website`, link a Maps y `status` (mini-CRM: `nuevo/contactado/descartado/convertido`).

Campos de actividad de reviews con **semántica corregida** (la Places API devuelve un set parcial, hasta 5 reviews "most relevant", no necesariamente las más nuevas):

- `last_review_at` — fecha de la review más reciente **observada** en el set devuelto.
- `recent_review_detected` — `True` si hubo al menos una review dentro de la ventana de la corrida (`max_days_since_review`).
- `review_activity_confidence` — proveniencia de la observación: **`full`** (el set cubre todas las reviews, `userRatingCount <= reviews_returned`), **`partial`** (Google devolvió un subconjunto; puede haber reviews más nuevas no vistas), **`unknown`** (sin `userRatingCount`).
- `reviews_returned` — cuántas reviews trajo la API (0–5).

> No se presenta "el negocio tuvo actividad reciente" como certeza; solo "la API devolvió al menos una review reciente".

### `reviews_snapshot`
Hasta 5 reviews que devuelve la API por lead, con su `publish_time`. Da trazabilidad de por qué un lead pasó el filtro de actividad reciente observada.

### `api_usage`
Log inmutable: **una fila por request HTTP a Google**. Método (`text_search`/`place_details`), SKU, status HTTP, latencia y `month_key` (YYYY-MM, indexado) para comparar contra cupos mensuales.

### `place_cache`
Caché de detalles por `place_id` (7 días, `DETAIL_CACHE_HOURS`). Guarda la respuesta cruda de Google en JSON para re-correr zonas **sin re-pagar** el SKU caro. Incluye candidatos descartados, así que un futuro re-scan los aprovecha.

### `webhook_deliveries`
Registro de cada intento de notificación webhook al cerrar una corrida (done o failed). Aislado: un fallo del webhook no afecta la corrida.

### `api_clients`
Clientes con API key (hash sha256) para auth por consumidor.

---

## 4. Cliente de Places API (`app/places_client.py`)

### Field masks (definen el SKU y el precio)

```python
TEXT_SEARCH_FIELD_MASK = "places.id,places.displayName,places.websiteUri"   # Paso 1 (Text Search Enterprise)
DETAILS_FIELD_MASK = "id,displayName,formattedAddress,nationalPhoneNumber,websiteUri,rating,userRatingCount,reviews,googleMapsUri"  # Paso 2 (Enterprise+Atmosphere)
```

Regla de billing clave (de `investigacion.md`): **si pedís un solo campo caro en una consulta, pagás todo el paquete caro**. `websiteUri` es un campo **Enterprise**, así que el Paso 1 se factura como Text Search Enterprise; igual conviene porque una consulta trae 20-60 candidatos. El Paso 2 (que necesita `reviews`, Enterprise+Atmosphere) paga el SKU caro solo por candidatos sin web que valen la pena.

### Métodos principales

- `text_search(zona, categoria, page_token, min_rating)` — Paso 1. Incluye `minRating` en el **body** de la request: Google filtra rating bajo *server-side*, sin subir el SKU.
- `text_search_all(...)` — itera la paginación (`nextPageToken`, máx 3 páginas) y devuelve `[{place_id, has_website}]`.
- `get_details(place_id)` — Paso 2, detail caro.
- `parse_details(raw)` — normaliza la respuesta de Google (normaliza `places/ID` → `ID`, parsea `publishTime` a datetime UTC, calcula `last_review_at` como el máx de las reviews **observadas** — el set puede ser parcial — y convierte reviews a objetos planos). También expone `reviews_returned` y `review_count` para derivar la confianza.
- `review_confidence(total_reviews, reviews_returned)` — `full` | `partial` | `unknown`: qué tan completo es el set devuelto para juzgar actividad.
- `filter_reason(parsed, min_rating, max_days)` — devuelve `None` si pasa, o el motivo de descarte: `has_website` | `low_rating` | `no_recent_review` (no se observó review reciente en el set devuelto).

### Registro de uso (`usage_cb`)

Cada request real a Google pasa por `_record()` que invoca un callback con `(method, sku, status_code, latency_ms)`. Esto permite loguear TODA llamada (éxito y fallo, con latencia) **sin acoplar** el cliente al modelo de datos. El worker inyecta un callback que acumula filas `ApiUsage`.

---

## 5. Worker (`app/tasks.py`) — el corazón

`run_search(search_id)` es el entry point sincrónico (para `BackgroundTasks`); internamente corre `_run_search_async`, que:

```
1. Marca search como "running"
2. Paso 1: text_search_all(zona, categoria, minRating=min_rating, lat/lng/radio, included_type) → candidatos con has_website
3. Fan-out concurrente (semáforo `details_concurrency`, default 5) — por candidato:
   a. ¿has_website?  → descartado (sin pagar detail)
   b. ¿en place_cache y fresco?  → usa caché, no paga
   c. ¿sí?  → get_details (paga Enterprise+Atmosphere) con retry ante 429/5xx, y guarda caché SIEMPRE
   d. filter_reason()  → low_rating / no_recent_review → descartado (contadores)
   e. pasa → upsert Lead + ReviewSnapshot (dedupe por place_id)
4. Persiste log de uso, calcula costo, marca "done"
```

Detalles de implementación:

- **Retry con backoff:** `_request()` en `app/places_client.py` reintenta `429`/`500`/`502`/`503` con backoff exponencial (respeta `Retry-After` si viene). Cada intento se registra en `api_usage` — el conteo del cupo refleja el tráfico real.
- **Concurrency:** los Place Details corren en paralelo con `asyncio.gather` + `Semaphore` (límite `details_concurrency`), respetando el QPS de Google.
- **Errores aislados:** un `place_id` que falla no rompe la corrida; se loguea, se hace `rollback` y se sigue con el siguiente. Solo un error del flujo general (p. ej. la key inválida) marca `failed`.
- **Upsert de leads:** si el `place_id` ya existe, se actualizan los campos y se recrean las reviews; si no, se crea. Evita duplicados entre corridas.
- **Acepta un `client` inyectable** para tests: los tests usan un `FakePlaces` que devuelve candidatos/details sin tocar la red.

### Post-procesamiento: scoring LLM

Después de marcar la corrida `done`, `run_search` ejecuta `_score_search_async` (`app/tasks.py`) si `SCORE_ENABLED=true`. Por cada lead de la corrida:

1. Se arma el input para el LLM **solo con campos que pesan en la rúbrica** (regla `investigacion.md:166`): `has_website`, `rating`, `review_count`, `last_review_at`, textos de reviews, `categoria`/`zona`. **Sin** teléfono/dirección.
2. Se llama a **OpenRouter** (`app/scoring_client.py`) con rúbrica ICP inline: sin web +30, rating-con-volumen +25, actividad reciente +25, rubro fit +20. Rangos: 85+ = `hot`, 60–84 = `warm`, <60 = `cold`.
3. Se fuerza salida **JSON estructurado** (`response_format: json_object`) y se valida con Pydantic (`LeadScoreResult`). Regla anti-alucinación: si falta un dato, el modelo asigna 0 e indica reason_code.
4. **Fallback automático de modelo**: si `openai/gpt-4.1-nano` falla, reintenta con `google/gemini-2.0-flash-lite` (ventaja de OpenRouter, `investigacion.md:207`).
5. Resultado: fila nueva en `lead_scores` (historial auditado) + campos denormalizados en el `Lead` (`fit_score`, `intent`, `score_model`, `scored_at`, `score_error`).
6. **Alcance (c):** si `intent="hot"` y el lead sigue en `status="nuevo"`, se auto-marca `lista_contacto` (no pisa acciones manuales del CRM).

El scoring es **aislado**: errores cuentan en `searches.score_errors` pero no marcan la corrida como fallida. Se puede re-ejecutar con `POST /api/searches/{id}/score`.

---

## 6. Cálculo de costo (`app/costing.py`)

Basado en `investigacion.md` (cupos gratis + precios por exceso):

| SKU | Llamada | Cupo gratis/mes | Exceso /1.000 |
|---|---|---|---|
| `text_search_enterprise` | Text Search (Paso 1, con `websiteUri`) | 1.000 | $35 |
| `enterprise_atmosphere` | Place Details (Paso 2, rating/reviews) | 1.000 | $25 |

- `monthly_totals(db, month)` — agrega llamadas por SKU de un mes: `calls`, `free`, `chargeable` (exceso sobre el cupo), `cost`.
- `search_cost(...)` — costo **atribuido** a una corrida: si el mes ya excede el cupo, la corrida paga en proporción a su share de llamadas del mes. Así una corrida aislada dentro del cupo cuesta $0, y las corridas viejas conservan su costo histórico.

---

## 7. API (`app/main.py`)

Toda ruta (salvo `/api/health`) requiere header `X-API-Key`.

| Método | Ruta | Descripción |
|---|---|---|
| POST | `/api/searches` | Dispara búsqueda (202, async). Body: `{zona, categoria, radio?, min_rating?, max_days_since_review?}`. Responde **acuse de recibo** (`id`, `status: pending`, `poll_url`) + header `Location` — no trae resultados |
| GET | `/api/searches/{id}` | Estado + contadores + costo estimado |
| GET | `/api/searches` | Lista de corridas (limit) |
| GET | `/api/searches/{id}/leads` | Leads de la corrida, filtrables (`has_website`, `min_rating`, `min_fit_score`, `intent`), paginados |
| GET | `/api/leads/{place_id}` | Detalle de un lead + reviews snapshot + score LLM |
| GET | `/api/leads/{place_id}/scores` | Historial de scores LLM del lead (reasoning + reason_codes) |
| POST | `/api/leads/{place_id}/status` | Mini-CRM: `nuevo/lista_contacto/contactado/descartado/convertido` |
| POST | `/api/searches/{id}/score` | Re-scoring LLM de la corrida (202, async) |
| GET | `/api/searches/{id}/export.csv` | Export CSV de los leads (UTF-8 BOM) |
| GET | `/api/usage?month=YYYY-MM` | Totales mensuales por SKU vs cupo + costo |
| GET | `/api/searches/{id}/usage` | Desglose de llamadas de una corrida |
| GET | `/api/health` | Health check (sin auth) |

Los defaults de filtro vienen de `app/config.py`: `DEFAULT_MIN_RATING=4.3`, `DEFAULT_MAX_DAYS_SINCE_REVIEW=90`.

---

## 8. Configuración (`app/config.py`)

Lee `.env`. Soporta dos formatos: `GOOGLE_PLACES_API_KEY=AIza...` o un archivo con **solo la key cruda** `AIza...` (autodetectado). Variables relevantes:

- `DATABASE_URL` — default `sqlite:///./data/search_leads.db`
- `API_KEY` — la API key del servicio (default `dev-key-123`)
- `DEFAULT_MIN_RATING`, `DEFAULT_MAX_DAYS_SINCE_REVIEW`
- `FREE_TS_ENTERPRISE_MONTHLY`, `FREE_ENTERPRISE_MONTHLY`, `COST_TS_ENTERPRISE_PER_1000`, `COST_ENTERPRISE_PER_1000`
- `DETAIL_CACHE_HOURS` — TTL del caché de detalles (default 168 = 7 días)
- `REQUEST_MAX_RETRIES` (3), `REQUEST_RETRY_BASE_DELAY` (1.0), `REQUEST_TIMEOUT` (20.0), `DETAILS_CONCURRENCY` (5)
- `USE_INCLUDED_TYPE` (true), `WEBHOOK_URL`, `WEBHOOK_ENABLED` (false), `WEBHOOK_TIMEOUT` (5.0)

---

## 9. Flujo de uso típico

```bash
# levantar
uvicorn app.main:app --reload --port 8001

# disparar una corrida
curl -X POST http://localhost:8001/api/searches \
  -H "X-API-Key: dev-key-123" -H "Content-Type: application/json" \
  -d '{"zona":"Nueva Córdoba","categoria":"gimnasios"}'

# ver estado y contadores
curl http://localhost:8001/api/searches/{id} -H "X-API-Key: dev-key-123"

# ver gasto mensual
curl http://localhost:8001/api/usage -H "X-API-Key: dev-key-123"

# monitoreo por consola
.\scripts\usage.ps1
```

---

## 10. Observaciones de diseño

- **SQLite en vez de Postgres/Redis:** v1 deliberadamente simple. `BackgroundTasks` reemplaza a Celery; si escala a múltiples workers o busca durability de jobs, migrar a Celery + Redis + Postgres sin cambiar la lógica del worker (ya es `client`-inyectable y con sesión propia).
- **El caché es el gran ahorro a escala:** re-correr la misma zona pasa de N llamadas caras a ~0 (`calls_expensive` real pasó de 20 → 3 → 0).
- **Contadores de descarte = auditoría de plata:** con `discarded_*` ves exactamente dónde se pierde cada candidato y cuánto ahorra cada filtro.