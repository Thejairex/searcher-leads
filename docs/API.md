# Search Leads — API

Referencia de implementación para consumir el servicio `search-leads`. Documenta cada endpoint, sus parámetros, schemas de respuesta con ejemplos JSON y errores.

---

## 1. Base y autenticación

| Ítem | Valor |
|---|---|
| Base URL | `http://localhost:8001` |
| Header | `X-API-Key: <API_KEY>` |
| Default dev | `X-API-Key: dev-key-123` (configurable via `API_KEY` en `.env`) |
| Formato | JSON (`Content-Type: application/json`) |

Toda ruta bajo `/api/` **excepto `/api/health`** requiere el header `X-API-Key`. Sin key (o inválida) → `401 Unauthorized`.

```http
X-API-Key: dev-key-123
Content-Type: application/json
```

---

## 2. Convención async (importante)

El `POST /api/searches` **no bloquea**: responde `202 Accepted` con un **acuse de recibo** (job encolado). Los contadores de resultado solo existen después de que el worker corre.

Flujo correcto:

```
1. POST /api/searches  -> 202 + { id, status: "pending", poll_url }
2. GET  {poll_url}     -> poll hasta que status = "done" | "failed"
3. GET  {poll_url}/leads -> resultados
```

El body del POST **no trae `total_candidates`/`total_leads`** (todavía no se procesó). Usá `poll_url` (o el header `Location`) para polling.

---

## 3. Tabla de endpoints

| Método | Ruta | Descripción | Status |
|---|---|---|---|
| GET | `/api/health` | Health check | 200 |
| POST | `/api/searches` | Dispara búsqueda (async) | 202 |
| GET | `/api/searches/{id}` | Estado + contadores + costo de una corrida | 200/404 |
| GET | `/api/searches` | Lista de corridas | 200 |
| GET | `/api/searches/{id}/leads` | Leads de la corrida (filtrables, paginados) | 200/404 |
| GET | `/api/leads/{place_id}` | Detalle de un lead + reviews + score | 200/404 |
| GET | `/api/leads/{place_id}/scores` | Historial de scores LLM del lead (reasoning + reason_codes) | 200/404 |
| POST | `/api/leads/{place_id}/status` | Cambiar status del lead (mini-CRM) | 200/404/422 |
| GET | `/api/usage` | Totales mensuales por SKU vs cupo | 200 |
| GET | `/api/searches/{id}/usage` | Desglose de llamadas de una corrida | 200/404 |
| POST | `/api/searches/{id}/score` | Re-scoring LLM de la corrida (async) | 202/404/409 |
| GET | `/api/searches/{id}/export.csv` | Export CSV de los leads de la corrida | 200/404 |

---

## 4. Endpoints en detalle

### 4.1 `GET /api/health`

Health check, **sin auth**.

**Respuesta 200:**
```json
{ "status": "ok", "db": "sqlite" }
```

---

### 4.2 `POST /api/searches` — disparar búsqueda

Dispara una búsqueda de leads en background. **Async**: responde `202` con acuse de recibo.

**Body (`SearchCreate`):**

| Campo | Obligatorio | Validación | Efecto |
|---|---|---|---|
| `zona` | ✅ | texto libre | Se concatena a la query de Google: `"{categoria} en {zona}"` |
| `categoria` | ✅ | texto libre | Tipo de negocio a buscar |
| `radio` | no | `100–50000` m | Radio en metros del centro. **Requiere `lat`/`lng`** |
| `lat` | no | `-90..90` | Latitud del centro para el radio |
| `lng` | no | `-180..180` | Longitud del centro para el radio |
| `included_type` | no | texto | Tipo oficial de Google (override del mapeo por categoría) |
| `min_rating` | no | `0–5` | Filtro server-side de Google (default `4.3`) |
| `max_days_since_review` | no | `1–365` | Filtro en código: review reciente en los últimos N días (default `90`) |

Si mandás `lat`+`lng` (+ opcional `radio`), la búsqueda se acota con `locationBias.circle` en Google (centro + radio). Sin `lat`/`lng`, `radio` se ignora.

**`included_type` automático:** si no lo mandás, el sistema resuelve la categoría a un tipo oficial de Google (ej. `"gimnasios"` → `gym`, `"tecnologia"` → `software_company`) y lo envía como `includedType` (filtro de precisión, no sube el SKU). Se puede desactivar con `USE_INCLUDED_TYPE=false`.

**Ejemplo request:**
```json
{
  "zona": "Buenos Aires",
  "categoria": "gimnasios",
  "lat": -34.6037,
  "lng": -58.3816,
  "radio": 5000,
  "min_rating": 4.3,
  "max_days_since_review": 90
}
```

**Respuesta 202 (`SearchAccepted`):**
```json
{
  "id": "359293cb-37e5-4f58-84dc-939012cf9a55",
  "zona": "buenos aires",
  "categoria": "tecnologia",
  "radio": null,
  "min_rating": 4.3,
  "max_days_since_review": 90,
  "status": "pending",
  "poll_url": "/api/searches/359293cb-37e5-4f58-84dc-939012cf9a55",
  "created_at": "2026-09-04T16:36:06.976101"
}
```
Header adicional: `Location: /api/searches/{id}`.

**Errores:**
- `401` — sin `X-API-Key`
- `422` — falta `zona`/`categoria`, o `min_rating` fuera de 0-5, etc.

---

### 4.3 `GET /api/searches/{id}` — estado de una corrida

Devuelve el estado y todos los contadores de diagnóstico de una búsqueda. **Usá este endpoint para el polling.**

**Respuesta 200 (`SearchOut`):**

| Campo | Significado |
|---|---|
| `status` | `pending` \| `running` \| `done` \| `failed` |
| `total_candidates` | candidatos que devolvió Google (Paso 1) |
| `total_leads` | leads que pasaron los filtros (Paso 2) |
| `calls_cheap` | llamadas Text Search Enterprise |
| `calls_expensive` | llamadas Place Details Enterprise+Atmosphere |
| `est_cost_usd` | costo estimado según cupos del mes |
| `discarded_has_website` | descartados por tener web (sin pagar detail) |
| `discarded_low_rating` | descartados por rating |
| `discarded_no_recent_review` | descartados por no observar review reciente |
| `reused_from_cache` | detalles reusados de caché (sin pagar) |
| `scored_leads` / `score_errors` | resultados del scoring LLM |
| `error` | mensaje si `failed` |

```json
{
  "id": "359293cb-37e5-4f58-84dc-939012cf9a55",
  "zona": "buenos aires",
  "categoria": "tecnologia",
  "radio": null,
  "min_rating": 4.3,
  "max_days_since_review": 90,
  "status": "done",
  "total_candidates": 20,
  "total_leads": 3,
  "calls_cheap": 1,
  "calls_expensive": 3,
  "est_cost_usd": 0.0,
  "discarded_has_website": 14,
  "discarded_low_rating": 0,
  "discarded_no_recent_review": 3,
  "reused_from_cache": 0,
  "scored_leads": 3,
  "score_errors": 0,
  "error": null,
  "created_at": "2026-09-04T16:36:06.976101",
  "updated_at": "2026-09-04T16:37:20.112304"
}
```
**Errores:** `404` si el `id` no existe.

---

### 4.4 `GET /api/searches` — listar corridas

Lista las búsquedas más recientes (default 20).

**Query params:**
- `limit` — 1 a 100 (default 20)

**Respuesta 200:** `list[SearchOut]` — array de los mismos objetos de 4.3, ordenados por `created_at desc`.

```json
[
  { "id": "359293cb-...", "zona": "buenos aires", "categoria": "tecnologia", "status": "done", "...": "..." },
  { "id": "22b2f1f5-...", "zona": "Nueva Córdoba", "categoria": "gimnasios", "status": "done", "...": "..." }
]
```

---

### 4.5 `GET /api/searches/{id}/leads` — leads de una corrida

Lista los leads cualificados de una búsqueda. **Paginado y filtrable.**

**Query params (todos opcionales):**

| Param | Tipo | Efecto |
|---|---|---|
| `has_website` | bool | `true`/`false` |
| `min_rating` | float | `rating >= valor` |
| `min_fit_score` | int 0-100 | `fit_score >= valor` (solo scored) |
| `intent` | `hot`\|`warm`\|`cold` | filtra por score del LLM |
| `page` | int ≥1 | paginación (default 1) |
| `limit` | int 1-100 | (default 20) |

**Ejemplo request:**
```
GET /api/searches/{id}/leads?intent=hot&min_fit_score=85&page=1&limit=20
```

**Respuesta 200:** `list[LeadOut]` — ver detalle de cada campo en 4.6. **Errores:** `404`.

---

### 4.6 `GET /api/leads/{place_id}` — detalle de un lead

Devuelve un lead completo: datos del negocio, reviews snapshot, score LLM y estado CRM.

**Respuesta 200 (`LeadOut`):**

| Campo | Significado |
|---|---|
| `place_id` | ID único del lugar en Google Maps |
| `name`, `address`, `phone` | datos del negocio |
| `rating` / `review_count` | rating y total de reviews |
| `has_website` / `website_uri` | si tiene web |
| `maps_uri` | link a Google Maps |
| `last_review_at` | review más reciente **observada** |
| `recent_review_detected` | ¿se observó review reciente en el set devuelto? |
| `review_activity_confidence` | `full` \| `partial` \| `unknown` |
| `reviews_returned` | cuántas reviews trajo la API (0-5) |
| `fit_score` | score LLM 0-100 |
| `intent` | `hot` \| `warm` \| `cold` |
| `score_model` | modelo usado (`openai/gpt-4.1-nano`) |
| `score_error` | error del scoring si falló |
| `status` | estado CRM: `nuevo`/`lista_contacto`/`contactado`/`descartado`/`convertido` |
| `reviews` | array de reviews (autor, rating, texto, fecha) |

```json
{
  "place_id": "ChIJxxxxxxxxxxxx",
  "search_id": "359293cb-37e5-4f58-84dc-939012cf9a55",
  "name": "NewBody",
  "address": "Av. Colón 1234, Nueva Córdoba",
  "phone": "0351-1234567",
  "rating": 4.8,
  "review_count": 120,
  "has_website": false,
  "website_uri": null,
  "maps_uri": "https://maps.google.com/?cid=123456",
  "last_review_at": "2026-07-31T18:43:37.635471",
  "recent_review_detected": true,
  "review_activity_confidence": "partial",
  "reviews_returned": 5,
  "fit_score": 80,
  "intent": "warm",
  "score_model": "openai/gpt-4.1-nano",
  "scored_at": "2026-09-04T16:38:00.000000",
  "score_error": null,
  "status": "nuevo",
  "reviews": [
    {
      "author": "Oliver Pochetti",
      "rating": 5,
      "text": "Great staff and friendly members...",
      "publish_time": "2024-10-10T19:07:51.850934"
    }
  ]
}
```
**Errores:** `404` si no existe.

---

### 4.7 `GET /api/leads/{place_id}/scores` — historial de scores LLM

Devuelve **todo el historial** de scores del lead (cada re-scoring genera una fila nueva).

**Respuesta 200:** `list[LeadScoreOut]` — ordenado por `created_at desc`:

| Campo | Significado |
|---|---|
| `model` | modelo que puntuó (`openai/gpt-4.1-nano`, fallback Gemini) |
| `fit_score` | score 0-100 |
| `intent` | `hot` \| `warm` \| `cold` |
| `reason_codes` | lista de códigos (`no_website`, `high_rating`, `recent_activity`, ...) |
| `reasoning` | justificación textual del modelo |
| `created_at` | cuándo se generó |

```json
[
  {
    "model": "openai/gpt-4.1-nano",
    "fit_score": 92,
    "intent": "hot",
    "reason_codes": ["no_website", "high_rating", "recent_activity"],
    "reasoning": "Sin web, rating alto, actividad reciente",
    "created_at": "2026-09-08T14:21:11.055838"
  }
]
```
**Errores:** `404` si el lead no existe.

---

### 4.8 `POST /api/leads/{place_id}/status` — mini-CRM

Cambia el estado de un lead para el equipo comercial.

**Body (`LeadStatusUpdate`):**
```json
{ "status": "contactado" }
```
Valores válidos: `nuevo` | `lista_contacto` | `contactado` | `descartado` | `convertido`.

**Respuesta 200:** el `LeadOut` actualizado (mismo schema de 4.6).

**Errores:**
- `404` — lead no existe
- `422` — status inválido

---

### 4.9 `GET /api/usage` — costos mensuales por SKU

Totales de llamadas a Google del mes, por SKU, comparados contra el cupo gratis.

**Query params (opcional):**
- `month` — formato `YYYY-MM` (default: mes actual)

**Respuesta 200 (`UsageTotalsOut`):**

| Campo | Significado |
|---|---|
| `month` | mes consultado |
| `by_sku[].sku` | `text_search_enterprise` \| `enterprise_atmosphere` |
| `by_sku[].calls` | llamadas del mes |
| `by_sku[].free` | cupo gratis mensual del SKU |
| `by_sku[].chargeable` | llamadas que exceden el cupo |
| `by_sku[].cost` | costo del exceso (USD) |
| `total_cost` | suma de costos |

```json
{
  "month": "2026-09",
  "by_sku": [
    {
      "sku": "text_search_enterprise",
      "calls": 1,
      "free": 1000,
      "chargeable": 0,
      "cost": 0.0
    },
    {
      "sku": "enterprise_atmosphere",
      "calls": 3,
      "free": 1000,
      "chargeable": 0,
      "cost": 0.0
    }
  ],
  "total_cost": 0.0
}
```

---

### 4.10 `GET /api/searches/{id}/usage` — desglose de una corrida

Detalle de cada llamada a Google que hizo una corrida.

**Respuesta 200 (`SearchUsageOut`):**
```json
{
  "search_id": "359293cb-37e5-4f58-84dc-939012cf9a55",
  "calls_cheap": 1,
  "calls_expensive": 3,
  "est_cost_usd": 0.0,
  "rows": [
    { "id": "...", "method": "text_search", "sku": "text_search_enterprise", "status_code": 200, "latency_ms": 1517, "created_at": "..." },
    { "id": "...", "method": "place_details", "sku": "enterprise_atmosphere", "status_code": 200, "latency_ms": 1236, "created_at": "..." }
  ]
}
```
**Errores:** `404`.

---

### 4.11 `POST /api/searches/{id}/score` — re-scoring LLM

Fuerza el re-scoring de todos los leads de la corrida. Genera **nuevas** filas en `lead_scores` (historial), sin pisar las anteriores.

**Respuesta 202:** `SearchOut` (acuse de recibo, contadores previos).
**Errores:**
- `404` — corrida no existe
- `409` — scoring deshabilitado o `OPENROUTER_API_KEY` no configurada

---

### 4.12 `GET /api/searches/{id}/export.csv` — exportar leads a CSV

Exporta los leads de la corrida en CSV (UTF-8 con BOM para Excel).

**Respuesta 200:** `text/csv; charset=utf-8` + header `Content-Disposition: attachment; filename="searches_{id}.csv"`.

Columnas: `place_id, name, address, phone, rating, review_count, has_website, website_uri, maps_uri, last_review_at, recent_review_detected, review_activity_confidence, reviews_returned, fit_score, intent, score_model, status, reviews, reviews_total`. La columna `reviews` concatena el texto de hasta 5 reviews separadas por `; `.

**Errores:** `404` si no existe.

---

## 5. Ejemplos de uso

### PowerShell

```powershell
$H = @{ "X-API-Key" = "dev-key-123" }
$base = "http://localhost:8001"

# 1. Crear búsqueda
$r = Invoke-RestMethod -Method Post -Uri "$base/api/searches" -Headers $H -ContentType "application/json" `
  -Body '{"zona":"Nueva Córdoba","categoria":"gimnasios"}'
$sid = $r.id
$poll = $r.poll_url

# 2. Polling hasta done
do {
  Start-Sleep -Seconds 5
  $s = Invoke-RestMethod -Uri "$base$poll" -Headers $H
} while ($s.status -in @("pending", "running"))
"status: $($s.status) | leads: $($s.total_leads)"

# 3. Leads hot
$leads = Invoke-RestMethod -Uri "$base$poll/leads?intent=hot" -Headers $H
$leads | ForEach-Object { "$($_.name) | fit=$($_.fit_score) | $($_.intent)" }

# 4. Cambiar status CRM
Invoke-RestMethod -Method Post -Uri "$base/api/leads/$($leads[0].place_id)/status" -Headers $H `
  -ContentType "application/json" -Body '{"status":"contactado"}'

# 5. Uso del mes
Invoke-RestMethod -Uri "$base/api/usage?month=2026-09" -Headers $H
```

### curl

```bash
H="X-API-Key: dev-key-123"
BASE="http://localhost:8001"

curl -s -X POST "$BASE/api/searches" -H "$H" -H "Content-Type: application/json" \
  -d '{"zona":"Nueva Córdoba","categoria":"gimnasios"}'

curl -s "$BASE/api/searches/{id}" -H "$H"
curl -s "$BASE/api/searches/{id}/leads?intent=hot&min_fit_score=85" -H "$H"
curl -s -X POST "$BASE/api/leads/{place_id}/status" -H "$H" -H "Content-Type: application/json" -d '{"status":"contactado"}'
curl -s "$BASE/api/usage?month=2026-09" -H "$H"
```

---

## 6. Glosario de campos clave

| Campo | Qué significa | Cómo NO leerlo |
|---|---|---|
| `status` | estado del proceso de búsqueda | — |
| `poll_url` | ruta relativa para consultar el resultado | no es el resultado en sí |
| `fit_score` | puntaje LLM 0-100 según rúbrica ICP | no es certeza absoluta |
| `intent` | `hot` (85+), `warm` (60-84), `cold` (<60) | |
| `recent_review_detected` | "la API devolvió al menos una review reciente" | **no** significa "el negocio está activo" |
| `review_activity_confidence` | `full` (set cubre todas las reviews) / `partial` / `unknown` | |
| `est_cost_usd` | costo estimado según cupos del mes | $0 no significa "gratis para siempre" |