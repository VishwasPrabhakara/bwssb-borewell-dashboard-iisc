# Design system — BWSSB Borewell Dashboard (IISc)

<!-- impeccable:design 1 -->

A civic groundwater monitoring dashboard, built as a docked app shell grounded in
India's official survey-map tradition. Operate mode: calm, authoritative, legible
from a laptop to a control-room wall. Zero-build (plain CSS, Leaflet + Chart.js).
The "world" contributes type, palette, density, and one signature move (the ward
map framed as an official survey sheet — a neatline border and a keyed legend);
navigation and controls are standard web components.

## Shell & layout

- `#app`: CSS grid, rows `auto 1fr`, full viewport height; `body` does not scroll.
- Top bar (`#topbar`): the committed deep-teal institutional band — wordmark, the
  five KPI cells as one docked panel, and the action icons.
- Workspace (`#workspace`): grid `rail | map | detail`. The left rail and right
  detail drawer collapse; nothing floats loose over the map.
- Rail collapses to a re-open handle (`.rail-open`); auto-collapsed below 720px so
  phones are map-first. Detail is a docked drawer that becomes an overlay ≤900px.
- Map (`#map-wrap`) is framed as a survey sheet via a neatline (`#map-wrap::after`).

## Color tokens (`:root`)

Shell (committed): `--shell #0e3b44`, `--shell-2 #10454f`, `--shell-ink #eef5f6`,
`--shell-muted #a7c3c8`, `--shell-line #1d525c`.
Ground & surfaces: `--ground #eceff0`, `--panel #ffffff`, `--panel-2 #f5f8f8`,
`--map-surround #e7ebeb`.
Ink & lines: `--ink #16242a`, `--muted #51626a`, `--faint #5f6f76`,
`--line #e1e7e8`, `--line-strong #cdd6d8`.
Interactive: `--accent #0e7d8c`, `--accent-ink #0a5c68`, `--accent-soft #e3f1f3`.
Status (ward trend, shared with the map JS `CRITICALITY_COLORS`): `--critical
#c23a2e`, `--rise #2f9c69`, `--stable #cf9a2c`, `--nodata #d4dadd` — refined from
the old pure RGB primaries. Data series: `--water #0e7490`, `--discharge #0891b2`.
Semantic: `--ok #1d7a54`, `--warn #b06a16`, `--danger #c23a2e`.

Strategy: Restrained-committed. The teal shell owns the top bar and rail edge
(colour at region scale, not a scattered accent); status colour is the map's
thematic layer; neutrals are tinted cool toward the shell hue. Light theme, chosen
from the use scene (daylit offices and bright control rooms).

## Type

System sans stack (`--font`) with `--mono` for sensor UIDs and a monospace feel in
tabular contexts. `font-variant-numeric: tabular-nums` on every figure that aligns
(KPIs, stat cards, tables, page counts). Scale: display/KPI 19–26px, section
headings 15–18px, body 13–14px, uppercase micro-labels 11px with ~1px tracking.
Headings use weight, not decoration. No display face is loaded (Operate mode is
well served by a workhorse stack; the character comes from the map framing and
palette, not a serif).

## Components

`--radius 10 / --radius-sm 7 / --radius-pill 999`. One hairline border (`--line`)
and one shadow family (offset + soft blur: `--shadow-sm`, `--shadow`,
`--shadow-drawer`, `--shadow-modal`). Chips (pill, status-coloured active states),
selects (custom chevron, `--accent-soft` focus ring), the control rail sections,
the Advanced `<details>`, the KPI cells, stat cards, the legend key (squares, not
dots), tabs, the quality table, and modals all share this corner, line, and spacing
language. Browser surfaces are themed: scrollbars, `::selection`, and
`:focus-visible` rings all come from the palette.

## Signature move

The ward choropleth reads as an official survey sheet: a fine neatline frame around
the map, the trend classes as the thematic fill, and a formally keyed legend block
in the rail — not a floating card.

## Motion

One restrained grammar: drawer and modal slide-ins on an ease-out curve from a
visible default; rail collapse transitions width (and calls `map.invalidateSize`).
Everything respects `prefers-reduced-motion`.

## Constraints that bind future work

Zero-build static; Leaflet + Chart.js from CDN; deploys as repo root on GitHub
Pages. The map `CRITICALITY_COLORS` in `js/ui/selection.js` must stay in sync with
the `--critical/--rise/--stable/--nodata` tokens (map fill and legend read them).
Preserve the element IDs the JS wires to.
