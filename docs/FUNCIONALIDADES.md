# Search Leads — Cómo funciona el sistema

Explicación conceptual de qué hace el servicio, su lógica interna y los flujos que lo componen. Para la referencia de endpoints, ver `docs/API.md`.

---

## 1. ¿Qué problema resuelve?

El servicio encuentra **leads comerciales cualificados**: negocios que podrían ser clientes de tu empresa. Un lead "bueno" cumple tres condiciones:

1. **No tiene sitio web** → probablemente le interesa una solución web (tu producto).
2. **Tiene buen rating** (≥ 4.3 por default) con volumen real de reseñas.
3. **Está activo** → tiene al menos una review reciente (últimos 90 días por default).

El sistema arma esto automáticamente consultando la **Places API (New) de Google** (vía legítima, no scraping), lo puntúa con un **LLM** y lo deja listo para el equipo de ventas como un mini-CRM.

---

## 2. El embudo de 2 pasos (el corazón del sistema)

La clave de diseño es **minimizar el costo de Google** dividiendo la búsqueda en 2 pasos (`app/places_client.py`):

```
POST /api/searches
      │
      ▼
PASO 1 — Text Search (barato por candidato)
      │  query: "{categoria} en {zona}"
      │  field mask: places.id, displayName, websiteUri
      │  una sola llamada devuelve 20-60 candidatos
      ▼
20 candidatos (ejemplo real "tecnologia en buenos aires")
      │
      ▼
POR CADA CANDIDATO:
  ¿tiene web? ────────────► descartado (sin gastar el detalle caro)
  │ no
  ▼
PASO 2 — Place Details (caro: Enterprise+Atmosphere)
      │  field mask: websiteUri, rating, reviews, teléfono, dirección
      ▼
  Filtros en código:
  ├─ rating ≥ min_rating?
  ├─ al menos 1 review reciente?  ──► descartado
  ▼
LEAD guardado + reviews snapshot
```

**Por qué ahorra plata:** el Paso 1 cuesta ~$0.0017 por candidato (una consulta trae muchos), mientras que cada Paso 2 cuesta ~$0.025 por candidato. Al descartar los que tienen web **antes** del Paso 2, evitás pagar el detalle caro de negocios que nunca iban a servir. En la corrida real: **20 candidatos → 3 leads**, se pagaron solo 3 detalles caros en vez de 20.

### 2.1 Candidatos crudos y detail desacoplado (nuevo)

Desde `d2e8032` el Text Search persiste **todos** los candidatos en `search_candidates` (`place_id`, `name`, `formatted_address`, `has_website`, `position`), antes del filtrado caro. Esto habilita:

- `GET /api/searches/{id}/candidates` — lista lo que trajo Google para esa corrida, barato (solo DB, paginado por `position`), sin tocar Place Details.
- `GET /api/candidates/{place_id}` — trae el detail completo de un candidato suelto (SKU caro `Enterprise+Atmosphere`), reutilizando `PlaceCache` (7 días) si está fresco (`cached=true` no paga, `cached=false` paga y guarda caché). Desacoplado de la búsqueda: no necesita `search_id` salvo que quieras `?promote=true&search_id={id}`.

`promote` hace upsert idempotente a `leads` por `place_id` si el candidato pasa los filtros de esa `search_id` (`min_rating`, `max_days_since_review`, `include_with_website`). Permite rescatar manualmente un candidato que el embudo automático descartó (ej. revisar un `has_website=true` o un `rating` límite) sin re-correr la búsqueda.

---

## 3. Filtros y la semántica honesta de las reviews

La Places API devuelve **hasta 5 reviews** por negocio, pero son las "most relevant", **no necesariamente las más nuevas**. Por eso el sistema es cuidadoso con lo que afirma:

| Campo | Qué dice en realidad |
|---|---|
| `last_review_at` | fecha de la review más reciente **observada** en el set devuelto |
| `recent_review_detected` | "la API devolvió al menos una review dentro de la ventana" — **no** "el negocio está activo" |
| `review_activity_confidence` | qué tan completo es el set para juzgar: **`full`** (el set cubre todas las reviews), **`partial`** (hay más reviews que no vimos, puede haber actividad oculta), **`unknown`** (sin dato de total) |

Esto evita presentar como certeza algo que solo se observa parcialmente. El filtro de "actividad reciente" descarta si no hay una review reciente **en el set devuelto**, no si el negocio está realmente inactivo.

---

## 4. Scoring LLM (OpenRouter)

Después de cada búsqueda, los leads pasan por un modelo de lenguaje (`openai/gpt-4.1-nano`, fallback `google/gemini-2.0-flash-lite-001`) que los puntúa con una **rúbrica fija** (Ideal Customer Profile):

| Criterio | Puntos |
|---|---|
| Sin sitio web | +30 |
| Rating alto con volumen real de reviews | +25 |
| Actividad reciente (negocio operativo) | +25 |
| Rubro que calza con lo que vende la empresa | +20 |

**Rangos de decisión:** `85+ = hot`, `60–84 = warm`, `<60 = cold`.

El modelo devuelve **JSON estructurado** (no opinión libre):
```json
{
  "fit_score": 80,
  "intent": "warm",
  "reason_codes": ["no_website", "high_rating", "recent_activity"],
  "reasoning": "Buen rating, reseñas recientes y actividad, pero sin sitio web"
}
```

Reglas del scorer:
- **No infiere datos faltantes** — si falta un dato, asigna 0 en ese ítem.
- **Solo recibe los campos que pesan** en la rúbrica (nunca teléfono ni dirección).
- Si el modelo primario falla, **hace fallback automático** al segundo modelo (ventaja de OpenRouter: una API, muchos proveedores).
- Cada resultado se guarda en el historial `lead_scores` (auditable, permite re-scoring).

**Costo:** ~$0.012 por 200 leads (los modelos nano/mini están diseñados para esto).

---

## 5. Optimización de costos de Google

### 5.1 SKUs y cupos (pricing oficial 2026-09)

| SKU | Qué llama | Cupo gratis/mes | Exceso /1000 |
|---|---|---|---|
| `text_search_enterprise` | Text Search con `websiteUri` | 1.000 | $35 |
| `enterprise_atmosphere` | Place Details con rating/reviews | 1.000 | $25 |

> Nota: `websiteUri` es un campo **Enterprise** (no Pro), por eso el Paso 1 se factura como Text Search Enterprise. Igual conviene: una consulta trae 20-60 candidatos.

### 5.2 Las 3 optimizaciones

1. **Skip temprano por web** — los candidatos con `websiteUri` del Paso 1 se descartan sin pagar el detalle caro.
2. **`minRating` server-side** — Google filtra el rating bajo en la query, sin subir el SKU.
3. **Caché de detalles** (`place_cache`, 7 días) — re-correr la misma zona no re-paga los Place Details de lugares ya vistos.

**Resultado medido en dev:** una corrida pasó de 20 detalles caros → **3** en la primera, → **0** en la repetición (todo de caché).

### 5.3 ¿Cuándo empieza a costar?

Con 50 búsquedas/mes + hasta 1.000 detalles → **$0** (dentro de los cupos). Empieza a costar al superar ~1.000 Text Searches o ~1.000 Place Details al mes. `GET /api/usage` muestra el estado de los cupos.

---

## 6. Flujo de una corrida (paso a paso)

```
POST /api/searches
  │
  ├─ Valida body (zona, categoria, filtros opcionales)
  ├─ Guarda Search con status="pending"
  ├─ Responde 202 + poll_url (acuse de recibo)          ← el POST NO bloquea
  │
  └─ WORKER (background)
     ├─ status="running"
     ├─ PASO 1: Text Search → candidatos con has_website
     │         └─ Guarda cada candidato en search_candidates (position)
     ├─ Por cada candidato:
     │    ├─ ¿web? → discarded_has_website++
     │    ├─ ¿caché fresco? → reused_from_cache++, sin llamar a Google
     │    ├─ Place Details (caro) + guarda caché SIEMPRE
     │    ├─ filtros: rating / review reciente
     │    └─ Lead guardado (upsert por place_id, dedupe)
     ├─ status="done" + contadores (calls, discards, cost)
     └─ SCORING LLM (si SCORE_ENABLED)
          ├─ por lead: OpenRouter → fit_score/intent
          ├─ guarda en lead_scores + actualiza el lead
          └─ si intent=hot y status="nuevo" → auto-marca "lista_contacto"
```

El cliente hace **polling** con `GET /api/searches/{id}` hasta ver `status: done`.

**Rama desacoplada (sin worker, bajo demanda):**
```
GET /api/searches/{id}/candidates          ← barato, DB, lista lo que trajo la corrida
GET /api/candidates/{place_id}             ← caro, desacoplado, Place Details + caché
GET /api/candidates/{place_id}?promote=true&search_id={id}  ← upsert a leads si pasa filtros de esa search
```
Útil para auditar por qué un candidato no llegó a lead (tinha web, rating, review) y rescatarlo manualmente sin re-correr toda la búsqueda.

---

## 7. Modelo de datos

| Tabla | Qué guarda |
|---|---|
| `searches` | cada corrida: zona, categoría, filtros, estado, contadores de costo/descarte/scoring |
| `search_candidates` | candidatos crudos del Text Search por corrida (`place_id`, `name`, `formatted_address`, `has_website`, `position`). Base para `GET /api/searches/{id}/candidates` |
| `leads` | negocios cualificados (PK `place_id`), datos, score LLM denormalizado, status CRM |
| `reviews_snapshot` | hasta 5 reviews observadas por lead (trazabilidad del filtro de actividad) |
| `api_usage` | **una fila por llamada HTTP a Google** (método, SKU, status, latencia, mes). Calls desacoplados de `GET /api/candidates/{place_id}` quedan con `search_id=NULL` (mes actual) |
| `place_cache` | Place Details crudos (7 días) para no re-pagar. Reusado por `GET /api/candidates/{place_id}` (`cached` flag) |
| `lead_scores` | historial de scores LLM por lead/modelo (auditable) |
| `webhook_deliveries` | intentos de notificación al cerrar una corrida (done/failed) |
| `api_clients` | consumidores con API key (auth) |

Dedupe natural: `leads` usa `place_id` como PK; re-correr una zona actualiza los leads existentes en vez de duplicarlos. `search_candidates` es append por corrida (un `place_id` puede aparecer en varias búsquedas, único por `search_id`).

---

## 8. Mini-CRM

Cada lead tiene un `status` que el equipo comercial maneja:

| Estado | Significado |
|---|---|
| `nuevo` | recién descubierto |
| `lista_contacto` | auto-marcado por el scorer cuando `intent=hot` |
| `contactado` | el equipo ya lo contactó |
| `descartado` | no interesa |
| `convertido` | se convirtió en cliente |

`POST /api/leads/{place_id}/status` lo cambia manualmente. El scorer **no pisa** decisiones manuales: solo auto-marca `lista_contacto` si el lead sigue en `nuevo`.

---

## 9. Límites actuales

- **`radio` requiere `lat`/`lng`** — funciona con `locationBias.circle` (centro + radio); sin coordenadas el radio se ignora.
- **BackgroundTasks en proceso** — sirve para un solo worker; si se necesita más escala, migrar a Celery + Redis + Postgres (la lógica ya está desacoplada para eso).
- **Una API key única para consumidores** — sin rate limiting por key aún.
- **`promote` es `GET` con side-effect** — `GET /api/candidates/{place_id}?promote=true` hace upsert (idempotente por `place_id`). No es REST puro, se documenta como tal; futuro puede migrar a `POST`.

### Búsquedas más precisas (`includedType`)

Si la `categoria` tiene match en el catálogo oficial de Google (ej. `"gimnasios"` → `gym`, `"tecnologia"` → `software_company`), se envía `includedType` como filtro de precisión. Es un filtro (como `minRating`), **no sube el SKU**. Se puede hacer override con el campo `included_type` del POST, o desactivar con `USE_INCLUDED_TYPE=false`. La lista está en `app/category_map.py` (~30 categorías comunes).

### Buscador simple vs avanzado

- **Simple:** solo `zona` + `categoria`. Usa los defaults: excluye negocios con web, `fetch_mode=optimized`, sin `target_leads`.
- **Avanzado:** expone todo el embudo de filtros — `min_rating`, `max_days_since_review`, `target_leads`, `fetch_mode`, `include_with_website`, `lat`/`lng`/`radio`, `included_type`.

**`target_leads` (máx 50):** el usuario define cuántos leads busca. El worker procesa candidatos hasta alcanzarlo o agotar el cupo de resultados de Google (máx ~60 por query). Si no llega al target, la corrida queda `done` con lo que encontró (no falla).

**`fetch_mode`:**
- `optimized` (default): Text Search barato (id+websiteUri) → Place Details solo para candidatos que pasan. Ahorra cuando muchos tienen web.
- `full`: pide rating/reviews/teléfono/dirección en el **mismo** Text Search (mask completo) → **1 llamada por página, sin Details**. Ideal para "traer todos los datos de una vez".

> **Nota de costos `full`:** traer todo en el Text Search sube el SKU a **Text Search Enterprise+Atmosphere ($40/1000)** en vez de Enterprise ($35/1000), pero **elimina los N Place Details caros** ($25/1000 cada uno). Cuándo conviene: cuando son pocos los candidatos que pasan el filtro de web. Ej. Buenos Aires "gimnasios": `full` = 1 llamada total; `optimized` = 1 Text Search + 3-20 Details. Si muchos candidatos tienen web (se descartan en `optimized` sin pagar detail), `full` paga por todo igual — elegir según el caso.

**`include_with_website`:** por default `false` (solo negocios sin web, el ICP). En avanzado se puede activar para incluir los que tienen sitio.

### Notificaciones (webhook)

Al cerrar una corrida (**done o failed**), si `WEBHOOK_URL` y `WEBHOOK_ENABLED` están seteados, se envía un POST JSON con el resumen (search_id, status, total_leads, scored_leads, est_cost_usd, URLs de leads y CSV). Cada intento se registra en `webhook_deliveries` — un fallo del webhook **no** afecta la corrida.

### Robustez del worker

- **Retry con backoff exponencial** ante `429`/`5xx` de Google (respeta `Retry-After` si viene). Cada intento se registra en `api_usage` (para ver el consumo real contra el cupo).
- **Fan-out concurrente** de Place Details con semáforo (`details_concurrency`, default 5) — respeta el QPS de Google y acelera corridas grandes.

---

## 10. Caso de uso completo

Supongamos: *"quiero leads de tecnología en Buenos Aires"*.

```
1. POST /api/searches
   {"zona":"buenos aires","categoria":"tecnologia"}
   → 202 { id: "359293cb-...", status: "pending", poll_url: "/api/searches/359293cb-..." }

2. Polling (cada 5s)
   GET /api/searches/359293cb-...
   → hasta ver status: "done"

   Resultado: 20 candidatos → 3 leads, 1 Text Search + 3 Place Details, $0.0,
   scored_leads: 3, score_errors: 0

3. Auditar candidatos (nuevo)
   GET /api/searches/359293cb-.../candidates
   → 20 candidatos crudos con has_website/position (barato, DB)
   GET /api/candidates/{place_id}
   → detail caro desacoplado (cached flag). Si un candidato con web te interesa:
   GET /api/candidates/{place_id}?promote=true&search_id=359293cb-...
   → upsert a leads si pasa filtros

4. Ver los leads
   GET /api/searches/359293cb-.../leads?intent=hot
   → los leads hot (fit_score ≥ 85) listos para ventas

5. Revisar un lead
   GET /api/leads/{place_id}
   → datos, reviews, fit_score, intent, confidence

6. Equipo comercial
   POST /api/leads/{place_id}/status  {"status":"contactado"}

7. Control de gasto
   GET /api/usage?month=2026-09
   → text_search_enterprise: 1/1000 gratis, enterprise_atmosphere: 3/1000 (+1 si usaste candidates detail sin caché), total $0
```

Ese es el ciclo completo: **buscar → auditar candidatos → filtrar → puntuar → vender → controlar el costo**.