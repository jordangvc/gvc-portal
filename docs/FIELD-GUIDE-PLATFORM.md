# Field Guide Platform

**Status:** Foundation shipped (2026-08-08). Live UX remains `web/fieldguide.html`;
scalable content lives in `content/fieldguide/` + `subsystems/fieldguide/{schema,validate,catalog,search,render}.py`.

This is the company field-guide / training platform — not a prettier one-off page.
A worker on a phone under time pressure must find how we do a task, what “good”
looks like, what changes on site, and what to open next.

---

## 1. Current repository findings

| Area | Finding |
|------|---------|
| SoT today | Monolithic `web/fieldguide.html` (~6.6k lines / ~500KB) with ~61 procedures, checkboxes, expert blocks, diagrams, Component Index |
| Strengths | Plain/Full toggle, role chips, home tiles, `.nextpath`, Job Check deep-links, checklist runs API, coach |
| Gap | Content triplicated (HTML + coach dict + tiles); no schema; weak synonym search; private CSS fork; coach covers ~18/61 |
| Coupling | Editing a procedure = editing a giant HTML file |
| New layer | Repo JSON catalog + normalize/validate/load/search/render + APIs |

**Pilot migrated:** Job Check drywall spine —
`framing`, `preboard-walk`, `hang`, `scrape`, `finish`, `level5-skim`, `cleanout`
(coat-stage chips share `finish`); Job Check gates —
`qc-walk`, `closeout-rhythm` (Notes + Completion Date anchors); Ops logistics —
`jobstart-firstday`, `job-conditions`, `window-returns`, `scaffold-lifts`
(Lock Box, Heater/Cans, Window type, Scaffolding, Open questions anchors);
**ACT ceilings** — `act` (grid layout, hanger wire, tile);
**Firestopping** — `firestop` (UL HW-D decode, annular space, dynamic joints);
**Safety orientation** — `safety-orient` (ops safety-basics);
**Change orders** — `changeorder` (ops money-scope).

## 2. Assumptions

- Monday Job Check stage labels remain the spine for drywall sequence.
- HTML shell stays the full library until procedures are migrated incrementally.
- Content authoring v1 = git (lien_rules pattern), not a CMS.
- Draft procedures never appear in default field search.
- No production rates in guides (existing Field Manual rule).

## 3. Module map

```
content/fieldguide/
  manifest.json          trades, categories/groups, featured, jobcheck_anchors
  procedures/*.json      one file per procedure (stable id)
  diagrams/              reusable SVG / assets (future)

subsystems/fieldguide/
  schema.py              normalize_* → canonical procedure
  validate.py            validate_procedure / validate_manifest
  catalog.py             load_catalog / get_procedure / catalog_summary
  search.py              synonym scoring / related_suggestions
  render.py              HTML fragments matching .doc / .plainwords / .steps / .nextpath
  coach.py / runs.py     existing (unchanged contracts)

app/service.py
  GET /ui/api/fieldguide/catalog
  GET /ui/api/fieldguide/search?q=
  GET /ui/api/fieldguide/procedure/{id}
```

## 4. Agent task breakdown (standing roster)

| Agent | Owns |
|-------|------|
| Coordinator | Priorities, PR integration, quality gates, hub rN |
| UX / IA | Trade landings, shallow nav, breadcrumbs, next-step rules |
| Design system | Tokens, tiles, callouts, mobile scan density (prefer `gvc.css`) |
| Content model | Schema fields, required-for-approved rules |
| Diagram / visual | Reusable SVG patterns, captions, annotation rules |
| Content template | task_guide / troubleshooting / quick_ref / checklist / tools |
| Search / retrieval | Tags, synonyms, filters, relevance |
| Workflow / page flow | No dead ends; every page answers the 8 field questions |
| Frontend | Shell + catalog hydrate; progressive article swap |
| Backend / content | Loaders, APIs, Dockerfile `COPY content` |
| QA / audit | Broken links, missing next_steps, incomplete approved |
| Governance | review_status, owner, last_reviewed, stale detection |

## 5. Content model (canonical after normalize)

**Procedure (required):** `id`, `title`, `trade`, `summary`, `steps[]`

**Procedure (approved-complete):** also `when_to_use`, `quality_checks`, `common_mistakes`, `synonyms`, `provenance.note`

**Also supported:** `lede`/`plain_words`/`short_answer`, `prerequisites`, `tools`, `materials`, `warnings`, `variations`, `troubleshooting`, `next_steps`, `related`, `tags`/`search_tags`, `jobcheck_stage`, `governance`, `diagrams`, `experts`, `template`

**Governance statuses:** `draft` | `review` | `approved` | `stale` | `archived`

**Warning kinds:** `stop` | `warn` | `note` | `tip` | `money` | `safety`

Authoring may use aliases (`trade_id`, `role_tags`, `related_ids`, flat `review_status`) — normalizer accepts both.

## 6. UX / navigation structure

```
Hub → Field Manual (/ui/fieldguide)
  Home: search (synonym-expanded) + role chips + trade/section tiles
  Procedure: plain words → when → needs → steps → warnings → variations
             → quality checks → mistakes → troubleshooting → what's next
  Deep link: /ui/fieldguide?coach=1#hang (Job Check)
```

Shallow: home → procedure → next procedure. No orphan pages: `next_steps` + `related` required for approved pilots.

## 7. Parallel tasks (next waves)

1. Migrate Job Check spine: frame → hang → scrape → taped/coats → finish → clean
2. Extract diagram assets into `content/fieldguide/diagrams/`
3. Wire coach `PROCEDURE_COACH` to catalog next_steps where ids match
4. Fixture trade stub (e.g. ACT) for template expansion
5. Stale-content job: flag `last_reviewed` older than `review_cycle_days`

## 8. Dependencies / blockers

- Full HTML→JSON migration is large; do spine-first.
- Diagram system needs a caption/annotation contract before mass SVG moves.
- Dark-mode / `gvc.css` migration of the shell is a separate design track.

## 9. Proposed / shipped code changes (this foundation)

- `content/fieldguide/**` pilots + manifest
- `subsystems/fieldguide/{schema,validate,catalog,search,render}.py`
- API routes + Dockerfile `COPY content`
- Home search synonym hydrate from catalog
- Tests: `tests/test_fieldguide_platform.py`

## 10. Validation / audit checks

- `validate_procedure(..., require_approved_complete=True)` on load
- Manifest groups only reference known ids
- Search: field synonyms (`scrapping`, `hang rock`, `knockdown`)
- Render includes `.nextpath` (no dead end)
- `python -m compileall` + pytest platform suite

## 11. Next highest-leverage steps

1. Migrate high-traffic repair pages (rated walls, touch-up, insulation)
2. Diagram assets under `content/fieldguide/diagrams/` with caption contract
3. Stale-content job: flag `last_reviewed` older than `review_cycle_days`

**Done (r71–r83):** Job Check spine + gates + Ops logistics + ACT + firestop + safety-orient +
changeorder in catalog; offline-baked `.nextpath`; shell sync inject from
catalog cards; bake script; alias map; next_steps link audit.

---

## Article template checklist (task_guide)

Every task guide should answer:

1. What is this? → title + short_answer / lede  
2. When do I use this? → when_to_use  
3. What do I need? → tools / materials / prerequisites  
4. What are the steps? → steps[]  
5. What changes on site? → variations[]  
6. What mistakes to avoid? → common_mistakes[]  
7. What to verify before moving on? → quality_checks[]  
8. What next? → next_steps[] + related[]
