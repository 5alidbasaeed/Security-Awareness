---
name: frontend-design
description: Design tokens and component conventions for the phishing-simulation platform's Django + HTMX + Chart.js dashboard — typography scale, spacing, colors, component patterns, HTMX conventions. Load before any template/UI work in this project.
---

# Frontend design system — phishing-simulation platform dashboard

Server-rendered admin/reporting dashboard (Django templates + HTMX + Chart.js). No SPA, no client-side router, no component framework. Internal/VPN-restricted audience for the admin surface; employees only see the training-completion pages via SSO.

## Typography

Base: 16px, system font stack (`-apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif`) — no webfont loading for an internal tool.

| Token | Size | Use |
|---|---|---|
| `text-xs` | 12px | table meta, timestamps, badges |
| `text-sm` | 14px | body text, form labels, table cells |
| `text-base` | 16px | default body |
| `text-lg` | 20px | card/section headings |
| `text-xl` | 24px | page headings |
| `text-2xl` | 32px | stat-tile numbers (e.g. org risk score) |

Line height: 1.5 for body text, 1.2 for headings. Never go below 14px for anything a user reads to make a decision (e.g. risk scores, employee names) — 12px is reserved for secondary metadata only.

## Spacing scale

4px base unit: `4, 8, 12, 16, 24, 32, 48, 64`. Use 16px as the default gap between form fields and 24px between page sections. Don't invent one-off pixel values outside this scale.

## Color tokens

Neutral-first palette with semantic color reserved for risk/status signaling only — don't use red/amber/green decoratively.

| Token | Value | Use |
|---|---|---|
| `text-primary` | `#1a1a1a` | body text |
| `text-secondary` | `#6b7280` | metadata, captions |
| `border` | `#e5e7eb` | table/card borders |
| `surface` | `#ffffff` | card/table background |
| `surface-muted` | `#f9fafb` | page background, alternating table rows |
| `accent` | `#2563eb` | links, primary actions, active nav |
| `risk-low` | `#16a34a` (green) | low risk score, compliant training status |
| `risk-medium` | `#d97706` (amber) | medium risk score, overdue-soon |
| `risk-high` | `#dc2626` (red) | high risk score, credential-attempt events, overdue training |

Contrast: body text on `surface`/`surface-muted` must hit WCAG AA (4.5:1). Risk colors are always paired with a text label or icon, never color alone (colorblind accessibility) — e.g. a risk badge reads "High" in red text/background, not just a red dot.

## Component patterns

- **Stat tile**: `text-2xl` number + `text-sm` label + optional trend indicator (▲/▼ with risk color). Used for org-wide/department risk score summaries.
- **Risk badge**: small pill, `text-xs`, background = risk color at 10% opacity, text = full risk color. Used inline in employee/campaign tables.
- **Data table**: `text-sm` cells, `surface-muted` alternating rows, sortable column headers are links (server-side sort via query param, not client JS sort).
- **Trend chart**: Chart.js line chart, x-axis = time, always show trend direction not just latest point (per the plan's scoring guidance — a snapshot alone is misleading). Use `risk-low`/`risk-medium`/`risk-high` for any threshold bands.

## HTMX conventions

- Partial templates live alongside their parent as `_<name>.html` (e.g. `employee_table.html` + `_employee_table_rows.html`), returned directly by the view for `hx-get`/`hx-post` targets.
- Use `hx-target` + `hx-swap="outerHTML"` for replacing a full component (e.g. re-rendering a table after a filter change); use `innerHTML` only for appending rows.
- Every `hx-get`/`hx-post` that can take >200ms (filter/search, campaign launch) gets an `hx-indicator` — don't ship a control with no loading state.
- Forms that submit via HTMX still validate server-side using Django forms — there is no client-side-only validation path.

## What NOT to do

- No React/Vue/client-side state management — if a screen seems to need it, that's a signal to reconsider the interaction, not to introduce a framework.
- No inline `style=""` attributes for anything beyond a one-off dynamic value (e.g. a computed chart color) — use the token classes above.
- Don't hardcode risk thresholds/colors in templates — pull them from the same constants the backend risk-scoring logic uses, so the UI and the scoring algorithm never drift apart.
