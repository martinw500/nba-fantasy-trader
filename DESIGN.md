---
name: Fantasy GM
description: Data-first fantasy basketball workbench. Dense stat tables, quiet chrome, one accent.
colors:
  bg: "oklch(97.4% 0.004 260)"
  panel: "oklch(100% 0 0)"
  panel-2: "oklch(98.6% 0.003 260)"
  line: "oklch(91.5% 0.006 260)"
  line-strong: "oklch(85% 0.008 260)"
  text: "oklch(23% 0.016 260)"
  muted: "oklch(49% 0.016 260)"
  faint: "oklch(62% 0.012 260)"
  accent: "oklch(51% 0.19 268)"
  accent-hover: "oklch(46% 0.19 268)"
  accent-soft: "oklch(95.5% 0.028 268)"
  accent-ink: "oklch(40% 0.17 268)"
  pos: "oklch(66% 0.15 152)"
  neg: "oklch(63% 0.19 24)"
  pos-ink: "oklch(42% 0.12 152)"
  neg-ink: "oklch(47% 0.17 24)"
  warn-soft: "oklch(95% 0.05 85)"
  warn-ink: "oklch(45% 0.1 70)"
  bg-dark: "oklch(17.5% 0.008 260)"
  panel-dark: "oklch(21% 0.009 260)"
  panel-2-dark: "oklch(23.5% 0.01 260)"
  line-dark: "oklch(29% 0.01 260)"
  line-strong-dark: "oklch(36% 0.012 260)"
  text-dark: "oklch(93% 0.006 260)"
  muted-dark: "oklch(72% 0.012 260)"
  faint-dark: "oklch(60% 0.012 260)"
  accent-dark: "oklch(66% 0.17 268)"
  accent-hover-dark: "oklch(72% 0.15 268)"
  accent-soft-dark: "oklch(30% 0.06 268)"
  accent-ink-dark: "oklch(82% 0.1 268)"
  pos-dark: "oklch(62% 0.14 152)"
  neg-dark: "oklch(60% 0.17 24)"
  pos-ink-dark: "oklch(80% 0.12 152)"
  neg-ink-dark: "oklch(78% 0.13 24)"
  warn-soft-dark: "oklch(30% 0.05 80)"
  warn-ink-dark: "oklch(85% 0.09 80)"
typography:
  body:
    fontFamily: '"Segoe UI Variable Text", "Segoe UI", system-ui, -apple-system, "Helvetica Neue", sans-serif'
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.45
  h1:
    fontFamily: "{typography.body.fontFamily}"
    fontSize: "22px"
    fontWeight: 650
    letterSpacing: "-0.01em"
  h2:
    fontFamily: "{typography.body.fontFamily}"
    fontSize: "15px"
    fontWeight: 650
  label:
    fontFamily: "{typography.body.fontFamily}"
    fontSize: "12px"
    fontWeight: 600
  stat:
    fontFamily: "{typography.body.fontFamily}"
    fontSize: "20px"
    fontWeight: 650
    letterSpacing: "-0.01em"
  stat-lg:
    fontFamily: "{typography.body.fontFamily}"
    fontSize: "28px"
    fontWeight: 700
    letterSpacing: "-0.02em"
  stat-sm:
    fontFamily: "{typography.body.fontFamily}"
    fontSize: "16px"
    fontWeight: 650
  small:
    fontFamily: "{typography.body.fontFamily}"
    fontSize: "13px"
    fontWeight: 400
  tag:
    fontFamily: "{typography.body.fontFamily}"
    fontSize: "11px"
    fontWeight: 650
rounded:
  bar: "2px"
  cell: "4px"
  control: "6px"
  panel: "8px"
  mark: "7px"
  bubble: "12px"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "20px"
  xxl: "24px"
components:
  button:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.text}"
    rounded: "{rounded.control}"
    padding: "6px 12px"
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "#ffffff"
    rounded: "{rounded.control}"
    padding: "6px 12px"
  button-primary-hover:
    backgroundColor: "{colors.accent-hover}"
  chip:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.text}"
    rounded: "{rounded.pill}"
    padding: "2px 9px"
  chip-pressed:
    backgroundColor: "{colors.text}"
    textColor: "{colors.bg}"
  panel:
    backgroundColor: "{colors.panel}"
    rounded: "{rounded.panel}"
  nav-current:
    backgroundColor: "{colors.accent-soft}"
    textColor: "{colors.accent-ink}"
    rounded: "{rounded.control}"
    padding: "7px 10px"
---

# Design System: Fantasy GM

## Overview

**Creative North Star: "The quiet stat desk"**

A working tool for one fantasy manager on a laptop. It is the category standard played straight: the density of Basketball Monster, the calm of Linear, the friendliness of Sleeper. Chrome stays neutral and out of the way so the numbers carry the color.

Every screen is a left nav, a sticky top bar (league, punt chips, refresh), and a single column of panels. Data shows up as tables and category strips, never as KPI tiles.

**Key Characteristics:**
- Cool grey ground, white panels, hairline borders.
- One indigo accent, used only for actions, the current nav item and selection.
- Green/red diverging z-score cells are the main color on screen.
- Punting a category greys that column out in every table at once (the signature move).
- Light and dark themes follow the OS, from the same tokens.

## Colors

### Primary
- **Indigo accent** (`accent`, `oklch(51% 0.19 268)`): primary buttons, current nav, pressed segmented buttons, focus rings, text selection. Never decoration.

### Neutral
- **Cool ground** (`bg`), **panel** (white) and **panel-2** (sidebar, row hover): three steps of the same cool hue 260.
- **line / line-strong**: hairline borders and control outlines.
- **text / muted / faint**: primary copy, secondary copy (≥4.5:1 on panels), tertiary labels only.

### Data
- **pos / neg**: fill colors for z-score cells, mixed with transparency up to 50% by magnitude (`|z| / 2.5`).
- **pos-ink / neg-ink**: the text versions of the same hues for signed numbers (`+1.20`, `-0.85`).
- **warn-soft / warn-ink**: the demo-data notice only.

### Named Rules
**The Numbers Own the Color Rule.** Chrome is neutral; saturated color appears only where it encodes a number's sign and size, or a primary action.

**The Dark Mirror Rule.** Dark mode redefines the same token names under `prefers-color-scheme: dark`. Components never branch on theme.

## Typography

One system sans for everything (Segoe UI Variable on Windows), tabular numerals on the whole page so columns line up.

### Hierarchy
- **h1** 22px / 650: one per view.
- **h2** 15px / 650: panel titles.
- **body** 14px / 400.
- **label** 12px / 600, muted: table headers, category labels, panel descriptions (12.5px).
- **small** 13px: notices, toasts. **tag** 11px / 650: injury/status tags.
- **stat** 20px / 650: category rank in the strip. **stat-sm** 16px / 650: the you/them/looks-to-them numbers. **stat-lg** 28px / 700: reserved for one large number per screen.

### Named Rules
**The No Eyebrow Rule.** No kicker labels above headings. A heading plus a one-line muted description is the whole pattern.

## Layout

- Shell: 216px sidebar + fluid main, content max 1360px, 24px gutters (16px on phones).
- Panels stack with a 20px gap. A panel is a header (title + one-line description + actions) over a body or a full-bleed table.
- `grid2` places two panels side by side; it collapses to one column under 980px.
- Below 760px the sidebar turns into a horizontally scrolling tab row and tables scroll inside their panel.

## Elevation & Depth

Flat with one soft lift: panels use `0 1px 2px` plus `0 4px 12px` at 5–6% alpha (deeper in dark mode). The top bar uses an 8px backdrop blur so content shows faintly through while scrolling. No other shadows.

## Shapes

Radii step 2 / 4 / 6 / 8 / 12 / pill: rank bars 2px, z-score cells and tags 4px, controls 6px, the brand mark 7px, panels 8px, chat bubbles 12px, chips and verdict pills fully round.

## Components

### Buttons
Default: white with a `line-strong` border. Primary: solid indigo. Ghost: no border, muted text, used for row actions ("Load", "Waivers"). Disabled drops to 50% opacity. A busy refresh button spins its icon.

### Chips
Punt chips and chat suggestions. Pressed punt chip inverts to text-on-bg with a strike-through, so a punted category is clear at a glance.

### Cards / Containers
Panels only. Never nest a panel in a panel.

### Navigation
Sidebar buttons with an 18px stroke icon (custom SVG, 1.7 stroke). The current item gets `accent-soft` fill and `accent-ink` text.

### Z-score table (signature)
Category cells are centered numbers on an inset 4px-rounded fill (`--c`) whose strength follows |z|. Punted columns get `opacity .3` + grayscale and a struck-through header. "Now" is green or red against "Value" to show hot or cold starts.

### Category strip
Equal columns per category: label, rank `n / 12`, signed total, and a 4px bar. The bar is green in the top 40%, red in the bottom 35%, grey between.

## Do's and Don'ts

### Do:
- Do show the reason with every verdict: per-category deltas next to the net number.
- Do show both sides of a trade (you, them, how it looks to them).
- Do use empty states that explain what the panel will show and how to fill it.
- Do keep transitions at 120–200ms and only on state changes.

### Don't:
- Don't add KPI tile grids or hero metrics.
- Don't use the accent for decoration, status or data.
- Don't use emoji or Unicode glyphs as icons; draw them in the sprite.
- Don't open a modal for anything; everything is inline.
