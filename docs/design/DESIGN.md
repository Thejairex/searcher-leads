# DESIGN.md — CRM ANAVIC (crm.anavicdigitals.com)

Documento de diseño extraído en vivo (DevTools + CSS real de producción) para replicar la interfaz.
Fuente de verdad: `docs/design/styles.css` (CSS completo de producción, 217 reglas) + capturas en `docs/design/captures/`.

- **Stack visual**: React SPA (Vite), CSS plano con variables CSS, Google Fonts (Poppins + Inter), SVGs inline stroke (estilo lucide, stroke-width 1.8, stroke-linecap/linejoin round).
- **Estética**: "Glassmorphism sobre fondo oscuro" con acento degradado magenta→violeta→azul. Theme claro y oscuro conmutables (el default tras login es oscuro; el login es siempre claro).

---

## 1. Paleta y tokens

### Tokens base (raíz)
```css
:root {
  --grad: linear-gradient(120deg, #E935C1 0%, #8B5CF6 52%, #4EA8F0 100%);
  --grad-soft: linear-gradient(120deg, rgba(233,53,193,.16) 0%, rgba(139,92,246,.16) 52%, rgba(78,168,240,.16) 100%);
  --glass: rgba(255,255,255,.62);
  --glass-strong: rgba(255,255,255,.8);
  --glass-border: rgba(255,255,255,.9);
  --ink: #1E1B2E;          /* texto principal */
  --ink-soft: #6B6580;     /* texto secundario */
  --ink-faint: #9A94AC;    /* texto terciario/placeholders */
  --radius-lg: 24px;
  --radius-md: 18px;
  --radius-sm: 12px;
  --yellow: #F5A524;
  --red: #F5445D;
  --green: #22C99E;
  --blue: #4EA8F0;
  --purple: #8B5CF6;
  --pink: #E935C1;
  --surface: #FFFFFF;
  --surface-border: rgba(139,92,246,.1);
  --input-bg: rgba(255,255,255,.7);
  --page-bg: #FFFFFF;
  --modal-bg: rgba(255,255,255,.92);
  --shadow-glass: 0 8px 32px rgba(120,80,180,.12), 0 1.5px 0 rgba(255,255,255,.6) inset;
}
```

### Tema oscuro (body.theme-dark) — el usado por defecto en la app
```css
body.theme-dark {
  --glass: rgba(13,12,22,.55);
  --glass-strong: rgba(15,14,25,.82);
  --glass-border: rgba(255,255,255,.07);
  --ink: #F2EFFB;          /* texto principal claro */
  --ink-soft: #ADA6C4;
  --ink-faint: #7A7495;
  --surface: #12111C;      /* tarjetas internas, kanban cards, burbujas ajenas */
  --surface-border: rgba(255,255,255,.06);
  --input-bg: rgba(255,255,255,.04);
  --page-bg: #07060D;      /* fondo casi negro */
  --modal-bg: rgba(11,10,18,.94);
  --shadow-glass: 0 8px 36px rgba(0,0,0,.55), 0 1px 0 rgba(255,255,255,.04) inset;
  color-scheme: dark;
}
```

### Colores puntuales observados en UI
| Uso | Claro | Oscuro |
|---|---|---|
| Código ticket (`.kcard-code`) | `#7C3AED` | `#B79CFF` |
| Badge contador kanban (`.kanban-count`) | bg `rgba(139,92,246,.14)` / texto `#7C3AED` | bg `rgba(183,156,255,.16)` / texto `#C6B4FF` |
| Link (`.link`) | `#7C3AED` | — |
| Tag default | bg `rgba(139,92,246,.1)`, texto `#7C3AED` | bg `rgba(183,156,255,.14)`, texto `#C6B4FF` |
| Tag origen (`.tag.origin`) | bg `rgba(78,168,240,.12)`, texto `#2E7FC1` | — |
| Tag industria (`.tag.industry`) | bg `rgba(233,53,193,.12)`, texto `#C22A9E` | — |
| Online / OK / Entrada | `--green #22C99E` (dot con halo `0 0 0 4px rgba(34,201,158,.18)`) | idem |
| Vence hoy (semaforo) | `--yellow #F5A524` + halo `0 0 0 4px rgba(245,165,36,.18)` | idem |
| Vencida | `--red #F5445D` + halo `0 0 0 4px rgba(245,68,93,.18)` | idem |
| Borde focus inputs | outline `2px solid rgba(139,92,246,.35)` offset 1px | idem |
| Overlay modal centrado | `rgba(30,27,46,.35)` + blur 4px | idem |
| Overlay anchor-topright | `rgba(10,8,18,.28)` + blur 4px | idem |

### Degradado de marca
`linear-gradient(120deg, #E935C1 0%, #8B5CF6 52%, #4EA8F0 100%)` — usado en: texto degradado (`.grad-text` con `background-clip:text`), nav activo, botones primarios, burbujas propias del chat, checkbox marcado, switch ON, avatares sin foto, línea superior de modales, íconos de acción, FAB, chips activos, pill-tabs activos.

## 2. Tipografía
- Google Fonts: `Poppins:wght@400;500;600;700;800` + `Inter:wght@400;500;600;700` (display=swap).
- Body: `Inter, Poppins, sans-serif`, 16px base, antialiasing, transición de color/fondo 0.25s al cambiar theme.
- Headings y displays: `Poppins, sans-serif` (h1–h4, .font-display).
- Escala observada:
  - Page title `h1.page-title`: 22px / 800 (Poppins). Palabra acentuada opcional en `.grad-text` (ej: "Reporte **diario**").
  - Page sub `.page-sub`: 13.5px, ink-soft.
  - Título de card `.card-title`: 15px / 700, flex gap 8px con ícono SVG 15px, margin-bottom 12px.
  - Stat number `.stat-num`: 24px / 800 (Poppins), normalmente con `.grad-text`.
  - Labels de formulario `.field-label`: 11px / 700 uppercase, letter-spacing .4px, ink-soft, mb 4px.
  - Labels de sección (drawer/sidebar) `.nav-group-title`: 10.5px / 700 uppercase, ls .8px, ink-faint.
  - Texto tabla `td`: 12.5px; header `th`: 10.5px / 700 uppercase ls .4px ink-faint.
  - Nombre en topbar pill: 12.5px / 700. Subtexto "En línea": 11px / 600 ink-soft con dot verde 7px.
  - Chat burbujas: 13px, line-height 1.4. Hora: 10.5px. Badge fecha: 11px / 700.
  - Botones: default 13px/700, `.btn-sm` 11.5px/700.
  - Código de ticket: 10px / 800, ls .4px, uppercase implícito por data.
  - Título kcard: 12.5px / 700, lh 1.3.

## 3. Fondo con "blobs"
Capa fija detrás de todo (login y app):
```css
.bg-blobs { position: fixed; inset: 0; z-index: 0; overflow: hidden; pointer-events: none; }
.blob { position: absolute; border-radius: 50%; filter: blur(70px); }
.blob1 { 520px; #E935C1; top:-160px; left:-120px; opacity:.11; }
.blob2 { 460px; #4EA8F0; top:220px; right:-140px; opacity:.10; }
.blob3 { 420px; #8B5CF6; bottom:-160px; left:30%; opacity:.09; }
.blob4 { 300px; #F5A524; bottom:120px; right:10%; opacity:.05; }
```
En dark, los blobs quedan muy tenues sobre el casi-negro (glow sutil violeta/azul).

## 4. Layout del app shell
```
body (page-bg)
└─ .app-shell  (flex, min-height 100vh, z-index 1)
   ├─ .sidebar   (272px, sticky top 0, height 100vh, glass + blur 22px, border-right glass-border, padding 20px 16px, z 5)
   │  ├─ .brand (flex, gap 10, padding 6px 8px 18px)
   │  │  ├─ .brand-logo-img (40px, radius 12px, bg blanco, padding 4px, sombra violeta)
   │  │  ├─ .brand-name.grad-text "CRM ANAVIC" (Poppins 16px/800, ls .2px)
   │  │  ├─ .brand-sub "ANAVIC AI SOLUTIONS" (10.5px/600 uppercase ls .6px ink-faint)
   │  │  └─ .sidebar-toggle (30px, radius 9px, bg purple .08, ícono hamburger) → colapsa a 76px
   │  ├─ .nav-shell > .nav-scroll
   │  │  ├─ .nav-item × 3 (Dashboard, Chat, Tareas)
   │  │  ├─ .nav-group-title "ESPACIO DE EQUIPO" (colapsable, chevron 12px)
   │  │  └─ .nav-item × 3 (Tickets, Calendario, Reporte)
   └─ .main (flex 1, padding 64px 34px 60px, position relative)
└─ .topbar-right (fixed top 14px right 28px, z 60, flex gap 10)
   ├─ .topbar-icon-btn × 3 (38px, círculo glass-strong blur 18px, ícono 17px) — campana, buscar, equipo(+badge online verde)
   └─ .topbar-pill (glass-strong blur 18px, radius 50px, padding 5px 12px 5px 5px, gap 9)
      ├─ .avatar.sm 28px (foto)
      ├─ nombre 12.5px/700 + status dot verde 7px + "En línea" 11px
      └─ theme-toggle (26px círculo bg purple .1; icono sol 13px en dark / luna en light; title "Cambiar a modo claro")
└─ FAB global (fixed left 58px bottom 20px, 52px círculo gradiente, sombra purple .4 0 8px 20px, ícono lápiz/nota)
```
- Sidebar colapsable (`.sidebar.rail-collapsed`): width 76px, padding 20px 10px, oculta textos; nav items centrados 11px padding.
- `topbar-online-badge`: badge verde 15px con borde 2px page-bg, bottom-right del ícono.
- Overlays fixed: `topbar-right` top 14 right 28.

### Header de página
```
.page-header (flex, align-items flex-end, justify-content space-between, mb 15px, gap 12, wrap)
   ├─ izq: h1.page-title + .page-sub (mt 4px)
   └─ der: acciones (botón gradient "＋ Nuevo ticket", "Histórico (9)" ghost sm, "Crear recordatorio", etc.)
```

## 5. Componentes

### Tarjeta glass `.glass.card`
- bg `--glass` + `backdrop-filter: blur(18px)`, border 1px `--glass-border`, radius 24px, shadow `--shadow-glass`, padding 20px.
- Dark: bg rgba(13,12,22,.55), border rgba(255,255,255,.07), shadow `0 12px 44px rgba(0,0,0,.55), inset 0 1px 0 rgba(255,255,255,.04)`.

### Botones
- `.btn`: inline-flex, gap 7px, padding 9px 16px, radius 12px, 13px/700, transición .18s.
- `.btn-primary`: bg grad, blanco, sombra `0 4px 14px rgba(139,92,246,.35)`; hover: sombra `0 6px 20px rgba(139,92,246,.5)` + translateY(-1px).
- `.btn-ghost`: bg `rgba(139,92,246,.08)` texto ink (dark: `rgba(255,255,255,.05)`, hover `.1`).
- `.btn-sm`: padding 6px 11px, 11.5px/700, radius 9px. Ej: "Nueva nota", "Enviar", "Ver historial", "Histórico (9)", "Cerrar sesión".
- `.btn-icon`: 28px círculo. Disabled: opacity .45.
- Variante "Crear recordatorio": gradiente pero estilo botón (flex con ícono).

### Inputs / selects / textarea
- `padding 9px 11px`, radius 11px, border 1px `rgba(139,92,246,.2)`, bg `--input-bg`, 13px, color ink.
- Focus: outline 2px `rgba(139,92,246,.35)` offset 1px.
- Con ícono a la derecha: wrapper `position:relative`, input `padding-right:36px`, span ícono 15px ink-faint posicionado right 11px.
- Selects: mismo estilo; en dark las options usan surface/ink.
- Labels `.field-label`: 11px/700 uppercase ls .4px mb 4px.

### Tabs tipo pill
`.pill-tabs`: contenedor inline-flex gap 4, bg `rgba(139,92,246,.06)`, padding 4px, radius 15px. `.pill-tab`: padding 8px 16px, radius 12px, 12.5px/700 ink-soft; `.active`: grad + blanco + sombra.

### Chips de cliente/proyecto (kanban)
`.client-chip`: padding 8px 14px, radius 12px, 12.5px/700, bg surface, border surface-border, ink-soft, flex con avatar circular 28px + nombre. `.active`: grad, blanco, border transparent, sombra `0 4px 12px rgba(139,92,246,.3)`.
Contenedor `.client-bar`: flex gap 8, scroll-x, padding-bottom 4px.

### Tags/badges
`.tag`: pill 10.5px/700, padding 3px 10px, radius 20px (variantes origin/industry arriba). `.nav-badge`: rojo, blanco, 10px/800, radius 20px, min-width 17px (en nav activo: blanco .35).

### Tablas (Tareas, clientes)
`.table-wrap`: th 10.5px uppercase ink-faint, padding 8px 10px, border-bottom `rgba(139,92,246,.12)`; td 12.5px padding 10px, border-bottom `rgba(139,92,246,.07)`; row hover bg `rgba(139,92,246,.04)`. `.cell-truncate`: max-width 240px ellipsis.
Checkbox circular `.round-check`: 18px, border 2px `rgba(139,92,246,.3)`; hover border `#8B5CF6`; checked: bg grad, sin border, sombra `0 3px 8px rgba(139,92,246,.4)`.
Semáforo `.semaforo`: 9px círculo + halo 4px (variantes hoy/vencida/ok).

### Kanban (Tickets)
- Contenedor `.kanban-wrap`: flex gap 16, overflow-x auto, padding-bottom 10px.
- Columna `.kanban-col`: min-width 250px, flex 1, glass blur 18, radius 18px, padding 12px; **borde superior 3px sólido por estado**: INGRESADO `#4EA8F0`, EN PROCESO `#8B5CF6`, EN ESPERA `#F5A524`, REVISIÓN `#E935C1`, FINALIZADO `#22C99E`.
- Título columna `.kanban-col-title`: 12px/800 uppercase ls .4px ink-soft, flex space-between + `.kanban-count` (pill 11px).
- Card `.kcard`: bg `--surface`, radius 14px, padding 12px, mb 10px, cursor grab, sombra `0 2px 8px rgba(100,70,160,.1)`, border `rgba(139,92,246,.08)` (dark: shadow `0 4px 14px rgba(0,0,0,.4)` + inset `0 1px 0 rgba(255,255,255,.03)`, border `rgba(255,255,255,.06)`); hover: translateY(-1px) + sombra mayor; dragging: opacity .4.
- Card layout: `.kcard-code` (10px/800, color `#B79CFF` dark), `.kcard-title` (12.5px/700, mt 4 mb 8), `.kcard-foot` flex space-between: stack de avatares 38px + fecha `DD/MM/YYYY` 10.5px ink-soft.
- Columna colapsable: 44px vertical.
- dragover: bg `rgba(139,92,246,.1)`.

### Chat
- `.chat-fullscreen`: margin 0 -34px -60px, height calc(100vh - 64px), flex row.
- Columna contactos (260px): input buscar (197px) + btn-icon tacho 28px; sección "DIRECTOS", "CANALES", "SIN CATEGORÍA" (labels 10.5px uppercase ink-faint); item contacto: flex gap 9, padding 8px, radius 12px, avatar sm 28px + nombre 12.5px/700 + preview 11px truncada; activo bg `rgba(139,92,246,.1)`. Canal: padding 6px 8px, radius 10px, ícono candado 12px + nombre 12.5px/700.
- Panel conversación `.glass` ( ocupa resto): header 57px padding 14px 18px con avatar + nombre 12.5px/700 + "Chat privado" 10.5px ink-faint, botones derecha 28px círculos (archivados, buscar).
- Mensajes: `.chat-bubble` max-width 72%, padding 9px 13px, radius 16px, 13px, mb 3px.
  - `.mine`: bg grad, blanco, margin-left auto, border-bottom-right-radius 4px.
  - `.theirs`: bg `--surface`, border `rgba(139,92,246,.12)`, border-bottom-left-radius 4px.
- Fila por mensaje: hora 10.5px ink-faint + acciones hover (28px círculos bg white .05): responder citando, reenviar, editar, eliminar.
- Separador fecha: pill bg surface, border white .06, radius 20px, padding 4px 14px, 11px/700 ink-faint, centrado.
- Composer (bottom): fila flex con iconos circulares 34px (adjuntar clip, @, emoji, mic — `.attach-link-btn` bg purple .08) + input flex-1 (mismo estilo inputs, placeholder "Escribí un mensaje... (Shift+Enter para salto de línea)") + `.btn-primary.btn-sm` "Enviar".
- Hover de mensaje: bg `rgba(139,92,246,.04)`.

### Calendario
- Filtros en glass card: checkboxes con labels 12.5px/600 (Reuniones, Tickets, Recordatorios — checkboxes cuadrados con gradiente checked) + select "Todos".
- Nav de mes: botones circulares prev/next (glass) + título "Septiembre 2026" 16px/700 (Poppins).
- Grilla 7 columnas (LUN…DOM): headers 10.5px/800 uppercase ink-faint centrados.
- Celda día: bg `--surface`, border 1px `rgba(255,255,255,.06)`, radius 12px, padding 6px, min-height ~80px; número 11px/800.
- Día actual: borde `#8B5CF6` + glow suave.
- Evento pill: bg `rgba(233,53,193,.16)`, texto `#C22A9E`, radius 7px, padding 2px 6px, 9.5px/700, ícono ticket 9px; "+N más" 9.5px ink-faint.

### Modales
- `.modal-overlay`: fixed inset 0, `rgba(30,27,46,.35)`, blur 4px, flex center, padding 24px, z 100.
- `.modal-overlay.anchor-topright`: align-start justify-end, padding `84px 28px 24px 24px`, bg `rgba(10,8,18,.28)` (drawer de usuario).
- `.modal-box`: bg `--modal-bg`, blur 24px, radius 24px, border glass-border, shadow `0 20px 60px rgba(80,50,140,.25)` (dark `0 24px 70px rgba(0,0,0,.6)`), max-width 720px, max-height 88vh, scroll.
- Línea decorativa: `::before` 2px alto, left/right 24px, top 0, radius 2px, bg `--grad`, opacity .75.
- Encabezado modal: flex gap 8 con ícono 15px + título 18.7px/700, mb 18px.
- Contenido interno padding ~20px. Labels uppercase 11px/700 ink-soft mb 4px. Inputs full-width mb 10px.
- Selector de usuario ("Asignado a"): pill bg `rgba(139,92,246,.08)`, radius 20px, padding 2px 8px 2px 2px, avatar 28px + nombre 11.5px/600.
- Footer: flex justify-end gap 8 — `Cancelar` ghost + `Crear ticket` primary.
- Modal usuario (anchor-topright, 420px): avatar lg 56px, nombre 20px/800, rol 12.5px ink-soft; fila superior derecha: `.btn-sm` "Cerrar sesión" + "Cerrar"; secciones "CAMBIAR ESTADO" (chips presencia: activo grad blanco, idle bg `rgba(139,92,246,.08)` radius 20px padding 6px 12px 11.5px/700), "✓ Entrada HH:MM:SS" verde 12.5px/700, "Salida" ink-soft; "MIS DATOS" y "MI PERFIL": filas `.profile-mini-btn` (flex gap 8, padding 8px 9px, radius 12px, bg `rgba(139,92,246,.06)`, 12px/600 ink-soft, mb 6px, ícono 15px; hover bg .08 texto ink).

### Popover notificaciones/equipo (`.team-popover`)
- 280px, bg `--surface`, radius 16px, border surface-border, shadow `0 16px 40px rgba(80,50,140,.28)`, padding 8px, max-height 360px scroll, z 70, posicionado bajo el botón (top 100%+10px).
- Encabezado "NO LEÍDAS" 10.5px uppercase ink-faint; filas `.team-popover-row` (flex gap 10, padding 8px 9px, radius 11px; hover bg `rgba(139,92,246,.07)`); pie con `.btn-ghost.btn-sm` "Ver historial" full-width.
- Nota: en producción el panel interno se ve con fondo surface oscuro #12111C.

### Perfil / estados
- Chips de presencia (dashboard): 4 spans — activo: grad, blanco, radius 20px, padding 5px 11px, 11px/700; inactivos: bg `rgba(139,92,246,.08)`, ink-soft.
- "Entrada HH:MM:SS" verde `#22C99E` 11.5px/700 con ícono check-circle; "Salida" ink-soft.
- `.status-dot` verde 7px (online), hay variantes por estado.
- Avatar: `.avatar` 38px círculo grad Poppins/700 con iniciales; `.sm` 28px, `.lg` 56px; con foto: `object-fit: cover` círculo.

### Empty states
`.empty-state`: centrado, padding 40px 20px, 13px ink-faint. Frases: "No tenés tareas pendientes. ¡Buen trabajo!", "Todavía no tenés notas adhesivas...", "Sin reportes aún.", "Todavía no hay reportes generados.", "Sin notificaciones nuevas."

### Scrollbar y animaciones
- Scrollbar 8px, thumb `rgba(139,92,246,.25)` radius 8px (hover .45).
- `.fade-in`: `fadeIn .25s ease` (opacity 0→1, translateY 4px→0). Aplicado a cards, popovers, modales.
- `@keyframes pulse` disponible (opacity 1→.35) para dots/presencia.

### Estados hover clave
- `.nav-item:hover`: bg `rgba(139,92,246,.08)`, color ink.
- `.kcard:hover`, `.topbar-pill:hover` (translateY -1px), `.topbar-icon-btn:hover` (translateY -1px), `.btn-primary:hover`, `.config-btn:hover` (translateY -2px), `.project-action-btn:hover` (translateX 2px), `.team-popover-row:hover`, `.table-wrap tr:hover td`, `.chat-canal-msg:hover`.

## 6. Páginas (spec de contenido)

### Login `/` (siempre tema claro, centrado)
- `.app-shell` centrado (align-items/justify-content center) + blobs + `.glass` card 400px, padding 36px, centrado.
- Logo tile 64px blanco radius 12 con sombra violeta + logo.png.
- "CRM ANAVIC" grad-text Poppins 19px/800; "Anavic AI Solutions" 11.5px/600 ink-faint. Todo centrado, mb 22px.
- Form: `Email` label + input con ícono mail derecha (mb 12px); `Contraseña` + input password con ícono ojo toggle (mb 6px); link "¿Olvidaste tu contraseña?" 12.5px right-aligned ink-soft (mb ~14px); botón `.btn-primary` full-width "Ingresar".
- Placeholder email: "tu@anavicdigitals.com".

### Dashboard `/dashboard`
1. `.page-header`: "Hola, Yair" (22px/800) + sub "miércoles, 9 de septiembre de 2026 · Rol actual: Miembro del equipo".
2. Grid 2 col (~1.3fr 1fr, gap 18px, mb 18px):
   - Card perfil: flex gap 16 — avatar lg 56px con foto; col: nombre 15px/700 "Yair Juarez", "Miembro del equipo · Anavic AI Solutions" 12px ink-soft, fila chips presencia; derecha: bloque check-circle + "Entrada 10:04:00" verde / "Salida 12:37:00" 11.5px/700.
   - Card "Resumen rápido" (ícono pin 15px): grid 2 col gap 12 — stat-num grad "0" + label 12.5px ink-soft "Tareas pendientes"; "27" + "Tickets activos".
3. Card "Tareas pendientes" (ícono check 15px) full-width mb 18px: empty-state o tabla de tareas.
4. Card "Notas adhesivas" (ícono lápiz) con `.btn-primary.btn-sm` "＋ Nueva nota" a la derecha del título; empty-state; notas en grilla (colores pastel).

### Chat `/chat`
- 3 zonas: nav sidebar (app) | lista contactos 260px | conversación glass.
- Ver §5 Chat. Composer abajo fixed dentro del panel.

### Tareas `/tareas`
- Header: "Tareas" + sub "Tareas pendientes de tus tickets asignados"; derecha `.btn-ghost.btn-sm` "🕘 Histórico (9)".
- Card full-width "Tareas pendientes" con tabla: checkbox circular | TAREA (12.5px) | TICKET (pill 10px/800 violeta bg `rgba(139,92,246,.1)` → dark bg `rgba(183,156,255,.14)`) | VENCE (semáforo verde 9px + halo).

### Tickets `/tickets`
- Header: "Tickets" + sub "Cada tarea de cliente se transforma en un ticket de desarrollo"; derecha `.btn-primary` "＋ Nuevo ticket".
- Card "📁 Proyecto" con `.client-bar` chips (avatar 28px + nombre; activo grad).
- Filtros: input "Buscar por código o título..." (280px) + select "Todos los asignados" (200px).
- Kanban 5 columnas (ver §5) con contadores, cards (código violeta, título, avatares 38px, fecha).

### Calendario `/calendario`
- Header: "Calendario" + sub "Reuniones por Meet, vencimientos de tickets y recordatorios"; derecha botón gradiente "📅 Crear recordatorio".
- Card filtros (checkboxes + select).
- Card calendario: nav mes + grilla 7×N (ver §5). Eventos con pills por tipo (vencimientos rosa, reuniones/recordatorios otros tonos).

### Reporte `/reporte`
- Header: "Reporte **diario**" (diario en grad-text) + sub "Se genera automáticamente al corte de las 00:00 hs con lo trabajado en el día".
- Grid: card lateral "Historial" (clock icon; lista de reportes; empty "Sin reportes aún.") + card grande con el reporte del día (empty: "Todavía no hay reportes generados.").

## 7. Assets y datos
- Logo: `https://crm.anavicdigitals.com/logo.png` (tile blanco radius 12 con sombra violeta).
- Fotos de usuario: `https://backend.anavicdigitals.com/storage/fotos/usuarios/*.png`.
- Iconografía: SVG inline stroke 1.8 (estilo lucide), 15–17px; se listan los paths usados en `shell.html` y `login.html` (mail, ojo, hamburger, home, chat, check-ticket, ticket, calendario, trending, bell, lupa, users).
- Idioma: español (rioplatense: "tenés", "Escribí", "Usá").

## 8. Capturas de referencia
| Archivo | Contenido |
|---|---|
| `captures/login.png` | Login claro 1920px |
| `captures/dashboard.png` | Dashboard oscuro 1920px |
| `captures/dashboard-light.png` | Dashboard claro 1920px |
| `captures/chat.png` | Chat completo oscuro |
| `captures/tareas.png` | Tabla de tareas |
| `captures/tickets.png` | Kanban 5 columnas |
| `captures/calendario.png` | Vista mensual |
| `captures/reporte.png` | Reporte diario |
| `captures/notif.png` | Popover notificaciones |
| `captures/modal-ticket.png` | Modal "Nuevo ticket" |

## 9. Reglas de oro para replicar
1. Todo componente "elevado" usa `.glass` (glass + blur 18 + border hairline + sombra violeta) — nunca sombras duras.
2. El gradiente de marca NO se usa como fondo de página, solo en acentos (pill activo, botones primarios, texto destacado, burbuja propia).
3. Radios: 24 cards/modales, 18 columnas kanban/contenedores, 12–14 inputs/cards internas, 9–13 botones/chicos, 50% para pills/círculos.
4. Espaciado: main 64/34/60; cards padding 20; gaps 8/10/12/16/18; mb 12–18.
5. Jerarquía de texto en 3 tintes (ink / ink-soft / ink-faint) + grad-text para números y palabras clave.
6. Microinteracciones: translateY(-1px) + sombra violeta en hover; fade-in .25s al montar.
7. Íconos siempre SVG stroke 1.8, nunca rellenos; tamaño 12–17px.
