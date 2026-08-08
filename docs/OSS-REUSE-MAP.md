# Open-source reuse map — GVC Portal + Takeoff

Research snapshot: 2026-08-08. Prefer **targeted reuse** over rewrites.
Portal stack = FastAPI + vanilla HTML/JS + WeasyPrint. Takeoff = separate React PWA.

---

## 1. Current app subsystems

| Subsystem | Where | Custom? | Notes |
|-----------|-------|---------|-------|
| Estimate / Invoice / CO PDFs | WeasyPrint + Jinja | Keep | Already on solid OSS (BSD) |
| Paid-by-check OCR | Vision + custom matcher | Keep matcher | Vision is the right cloud OCR |
| Job Start / Job Check / Monday | Custom adapters | Keep | Company SoT — no substitute |
| Field Manual / training | `web/fieldguide.html` + `content/fieldguide/` | **Hybrid** | Schema is right; don’t rebuild as Docusaurus |
| Billing / Monday search | Custom multi-leg | Keep | Domain-shaped |
| COI stamp | pypdf + reportlab | Keep | Geometry is ACORD-specific |
| Takeoff geometry | **External** React PWA | Study OSS | Not in this repo |
| LiDAR / point cloud | **Not built** | Adopt when needed | |
| Access / grants / activity | Custom GCS + Logging | Keep for now | |

---

## 2. Best reuse opportunities (top 5 — stop reinventing)

### 1) Field-guide search & findability
**Today:** custom synonym scorer + HTML tile filter.  
**Reuse:** [Fuse.js](https://www.fusejs.io/) (MIT) for client fuzzy over cards; later [Pagefind](https://pagefind.app/) (MIT) if the HTML corpus grows past ~200 pages.  
**Action:** **Use Fuse directly** (vendor one file) *or* keep server catalog search (already good for spine) and call `/ui/api/fieldguide/search` from the shell.  
**Still custom:** GVC synonyms, Job Check stage aliases, governance filters.

### 2) Field-guide IA / templates / related content
**Today:** JSON schema + HTML shell (dual SoT).  
**Reuse:** **Study** [Starlight](https://starlight.astro.build/) / Capsa patterns (frontmatter, sidebar, next/prev, Pagefind) — **do not** replace the portal with Astro.  
**Action:** **Copy patterns** into `content/fieldguide/` (you already have the right model). Finish wiring shell ↔ catalog (nextpath, coach related).

### 3) Blueprint PDF takeoff / measurement (Takeoff app)
**Today:** Custom React PWA.  
**Reuse:** **Study** [OpenTakeoff](https://github.com/Kentucky-ai/opentakeoff) (Apache-2.0) — pdf.js + canvas scale/area/count; [elstruck/pdf-takeoff](https://github.com/elstruck/pdf-takeoff) (MIT) for simpler measure UI.  
**Action:** **Adapt patterns / small modules** into Takeoff — do not replace portal. Keep portal seam = estimate JSON only.

### 4) PDF annotation / review overlays
**Today:** iframe native PDF preview only.  
**Reuse:** Mozilla [PDF.js](https://github.com/mozilla/pdf.js) (Apache-2.0) + study `@pdf-annotator/pdf-annotator` (MIT) or `react-pdf-highlighter` lineage.  
**Action:** **Use PDF.js** in Takeoff/portal preview when annotation is required. Avoid commercial SDKs / PandaDoc.

### 5) LiDAR / point-cloud viewing
**Today:** nothing.  
**Reuse:** [three.js](https://threejs.org/) (MIT) + [potree-core](https://github.com/tentone/potree-core) (MIT wrapper). Upstream Potree license is custom — prefer potree-core.  
**Action:** **Use directly** in a future Takeoff/field module when Jordan prioritizes scan viewing. Portal stays out of WebGL.

---

## 3. Recommended libraries (install / vendor)

| Library | License | Fit | Decision |
|---------|---------|-----|----------|
| **Fuse.js** | MIT | Field Manual fuzzy search in browser | **Use** (vendor) when offline/client search needs typo tolerance |
| **Pagefind** | MIT | Static full-text over rendered HTML | **Use later** if corpus ≫ catalog JSON |
| **pdf.js** | Apache-2.0 | Plan/PDF render | **Use** in Takeoff (likely already) |
| **pdf-lib** | MIT | Client PDF assemble | **Use** in Takeoff ingest patterns |
| **weasyprint / pypdf / reportlab** | BSD | Already in portal | **Keep** |
| **three.js + potree-core** | MIT | Point clouds | **Use when building LiDAR** |
| **Tesseract.js** | Apache-2.0 | Client OCR | **Avoid for checks** — Vision already wins; maybe Takeoff page labels only |

---

## 4. Recommended repos to study (not install wholesale)

| Repo | License | Borrow |
|------|---------|--------|
| **Starlight (Astro)** | MIT | Frontmatter schema, sidebar groups, “next page”, search UX |
| **Capsa / Clarify** | check each | ⌘K palette, MDX component callouts — ideas only |
| **OpenTakeoff** | Apache-2.0 | Scale calibration, area/linear/count canvas, IndexedDB persistence |
| **Frugal-Takeoff** | check | Konva annotation layers, multi-measure merge — study carefully |
| **react-pdf-highlighter(+)** | MIT | Viewport-independent highlight coordinates |

---

## 5. License cautions

- **Prefer MIT / Apache-2.0 / BSD** — matches current `requirements.txt`.
- **Avoid AGPL/GPL** for anything shipped in the portal Cloud Run image or Takeoff PWA unless counsel approves (copyleft can force source disclosure of combined work).
- **Potree upstream** — verify license text before embedding; prefer **potree-core** (MIT).
- **PandaDoc** — hard ban for GVC forward work (standing rule).
- Commercial PDF SDKs (PSPDFKit, etc.) — unnecessary cost if pdf.js covers the need.

---

## 6. Fastest wins (ordered)

1. **Wire Field Manual shell → catalog** (nextpath inject, procedure HTML API, coach related merge, home → `/search`) — **done r74**.  
2. **Vendor Fuse.js** only if client fuzzy over *all* HTML tiles is still weak after catalog search boost.  
3. **Takeoff:** read OpenTakeoff `oneclick.ts` / scale patterns before writing more measure code.  
4. **Extract fieldguide SVGs** into `content/fieldguide/diagrams/` (reuse render `diagram_ids`).  
5. **Defer** Docusaurus/Starlight migration — wrong runtime for an authenticated portal tool.

---

## 7. Proposed integration steps

### Field guide (portal) — continue current platform
1. Keep `content/fieldguide/*.json` as SoT for migrated procedures.  
2. Shell keeps rich HTML (experts/diagrams) until extracted.  
3. Catalog owns search metadata, next_steps, governance.  
4. Optional: `web/vendor/fuse.min.js` + index from `/catalog` cards.  
5. Do **not** stand up a second docs site.

### Takeoff (separate repo)
1. Confirm pdf.js version; align with OpenTakeoff patterns for scale + polygon area.  
2. Export remains `example_estimate.json` contract.  
3. LiDAR: spike potree-core in Takeoff only.

### Portal PDF preview
1. Keep iframe for generated WeasyPrint PDFs.  
2. Add pdf.js only if office needs in-browser highlight/review on source plans.

---

## 8. First code changes (this PR)

- Re-sync `hang`/`scrape` JSON step fidelity from HTML.  
- `/ui/api/fieldguide/procedure/{id}` returns rendered `html`.  
- Shell injects catalog `.nextpath` on spine docs (kills dead-ends).  
- Coach merges catalog related/next into related list.  
- Render uses `data-go` buttons + `span.txt` (shell-compatible).

---

## What must stay custom

Monday board writes, Stripe invoice lifecycle, Job Start handoff packet, COI ACORD geometry, GVC brand + access grants, estimate/invoice business rules, Morning Brief Slack routing (never `#operations`).
