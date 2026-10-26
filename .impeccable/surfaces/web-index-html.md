---
version: 1
slug: "web-index-html"
primary_target: "web/index.html"
related_targets: []
---

Scope: web/index.html, the whole local GM app. Mode: Operate. Built code-first.
Audience/job: one manager on a laptop deciding trades, add/drops and punts for their Yahoo NBA team; acts on Yahoo afterwards.
Constraints: plain HTML/CSS/JS single file, stdlib Python server, works offline except fonts/AI.

## Direction contract
THESIS: A data-first fantasy GM workbench at Basketball Monster density with Linear's quiet polish and Sleeper's friendliness. Refuses the dark neon sports dashboard and the KPI-tile grid.
OWN-WORLD: Cool light-grey app ground, white panels, hairline borders, one indigo accent for actions and selection only. Data colour is a diverging green/red z-score cell scale; dark theme mirrors it.
STORY: The manager sees where their team stands per category, what to do now (adds, sells), tests and finds trades judged from both sides, then asks the AI GM.
FIRST VIEWPORT: Left nav (Team, Trades, Waivers, Hot & Cold, AI GM). Top bar: league, mode, punt chips, refresh. Team view: category rank strip with H2H record, then the roster table as a z-score heatmap.
FORM: canon (standing exit, category standard played straight), seed: seed:8715af89358b31e4. Signature move: punt dimming. Toggling a punt chip greys that column in every table and re-ranks every list at once.
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
