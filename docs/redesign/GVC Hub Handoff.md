# GVC Hub — home screen handoff

Prerequisite: `gvc-ui.css` installed, `GVC Design Style.md` read. Every class below comes
from that stylesheet — write no new CSS. Reference design: `GVC Hub Home.dc.html`.

---

## 1. What changes

The tool grid stops being the home screen. It becomes **navigation in a left rail**, and
the main area becomes **the logged-in person's own hub** — what needs them today, their
numbers, their queue, their pinned jobs, their activity.

Nobody should have to pick a tool to find out whether anything needs them.

**Phone is primary.** ~90% of use is a phone in a truck. Build and test the phone layout
first; desktop is the wide version of the same screen, not the other way round.

---

## 2. Shell

```
DESKTOP (≥900px)                      PHONE (<900px)
┌──────────┬───────────────────┐      ┌───────────────────┐
│ .rail    │ .topbar           │      │ ☰  Your hub    JD │  .topbar
│ 264px    ├───────────────────┤      ├───────────────────┤
│ search   │ Needs you today   │      │ Greeting          │
│ groups   │ Metrics (4)       │      │ NEEDS YOU TODAY   │  ← first screen
│ nav      │ Queue │ Pinned    │      │ Metrics (2×2)     │
│          │       │ Activity  │      │ Queue             │
│ user     │ Jump back in      │      │ Pinned · Activity │
└──────────┴───────────────────┘      ├───────────────────┤
                                      │ Home Search Alerts Tools │ .tabbar
                                      └───────────────────┘
```

Markup is the `.app` / `.rail` / `.topbar` / `.page` / `.tabbar` skeleton from the class
reference. The breakpoints are already in the CSS: below 900px the rail hides (add
`.is-open` to show it as a full-screen drawer) and `.tabbar` appears.

---

## 3. Left rail

All 17 tools, grouped by what you're doing — not alphabetically, not by team:

| Group | Tools |
| --- | --- |
| Today | Your Morning Brief · GM Morning Huddle · Owner Pulse · Activity |
| Estimating & bids | Takeoff · Estimate Generator · Change Order · Job Start |
| Money | Billing Hub · Invoice Generator · Paid by Check · Lien Watch |
| Field | Job Check · Field Manual |
| Paperwork | COI Generator · Time Off |
| Company | Admin |

```js
const TOOLS = [
  ['Today', [['Your Morning Brief','all'], ['GM Morning Huddle','gm'], ['Owner Pulse','owner'], ['Activity','all']]],
  ['Estimating & bids', [['Takeoff','all'], ['Estimate Generator','office'], ['Change Order','office'], ['Job Start','office']]],
  ['Money', [['Billing Hub','office'], ['Invoice Generator','office'], ['Paid by Check','office'], ['Lien Watch','office']]],
  ['Field', [['Job Check','all'], ['Field Manual','all']]],
  ['Paperwork', [['COI Generator','office'], ['Time Off','all']]],
  ['Company', [['Admin','owner']]],
];
// grant tiers: 'all' < 'office' < 'gm' < 'owner'; a user carries the list of tiers they hold
```

- **Group headers** use `.kicker .kicker-sm`.
- **Badges** (`.badge`) ride on items that need this person — Billing Hub 7, Job Check 3.
  Refresh the counts on an interval while the tab is open and again on window focus. A
  stale count is worse than no count.
- **Grants dim, they don't hide** — `.nav-item.is-locked` with an em dash where the badge
  would sit. People learn the hub has an Estimate Generator and ask for access instead of
  never knowing it exists.
- **One `.is-active` item at a time.**
- Rail foot: `.avatar`, name, role, sign-out.

---

## 4. The personal hub

Everything comes from **one payload per user**, so the screen is a render, not a pile of
widget queries.

```ts
type HubPayload = {
  user:     { name, initials, title, homeTool };  // homeTool set per user in Admin
  greeting: string;                                // "Good morning, Jordan."
  summary:  string;                                // one sentence, the state of the day
  needs:    Array<{ kind, amount, title, detail, action, href }>;
  metrics:  Array<{ label, value, foot }>;         // exactly 4
  queue:    { title, link, href, rows: Array<{ name, sub, tag, flagged }> };
  pinned:   Array<{ name, sub, href }>;
  activity: Array<{ text, when, href }>;
  recent:   string[];
};
```

### Needs you today — the point of the screen

Two to four items with this person's name on them.

```html
<div class="card card-pad card-note stack">
  <div class="cluster" style="justify-content:space-between">
    <span class="kicker kicker-sm">Change order</span>
    <span class="num num-sm" style="color:var(--color-accent-ink)">$4,180</span>
  </div>
  <h3>CO #2 — 4115 Witler | Drees</h3>
  <p class="muted">Added soffit framing and two ceiling patches. Priced by Mark,
     waiting on your approval to send.</p>
  <div class="cluster">
    <button class="btn btn-primary">Review &amp; approve</button>
    <button class="btn">Open job</button>
  </div>
</div>
```

Wrap the set in `.grid-cards`. On phone the action button takes `.btn-lg .btn-block`.

**Compute it, never curate it.** Union of, filtered by grant:

- approvals pending this person (change orders, estimates, invoices approved-to-send)
- jobs blocked where they are ops owner
- past-due invoices they own
- complete-but-unbilled jobs (office roles)
- job checks owed from yesterday (field roles)

Cap at 4, sort by money-at-risk then age. This eventually feeds from the Slack canvas and
the agents — keep the payload shape so that swap is one adapter.

**Clear state:** replace the card row with a single line — "You're clear. 6 crews out,
nothing blocked, nothing waiting on you." — and let the metrics and queue carry the
screen. Never render an empty container.

### Metrics

Exactly four, role-specific, in `.grid-metrics`. Label is `.kicker .kicker-sm`, figure is
`.num .num-lg`, one line of context in `.muted` underneath. Collapses to 2×2 on phone
automatically.

### Queue

The person's working list in a `.card.card-flush` of `.row` items, titled for the role —
Jordan: *Exceptions* · Andrea: *Billing queue* · Robert: *Your route today*. Right-hand
`.tag` is `.tag-alert` when flagged, plain otherwise. Wrap the rows in `.scroll-rows`.

### Pinned · Activity · Jump back in

Pinned is user-editable (pin control on the row, stored per user). Activity is the audit
trail — every send, approval and status change writes to it, and Owner Pulse reads the
same source. "Jump back in" is the last five tools as `.chip` links.

---

## 5. Roles

Three payload shapes today. `homeTool` decides where a cold link lands.

| | Jordan (Owner) | Andrea (Billing) | Robert (Field) |
| --- | --- | --- | --- |
| Home tool | Owner Pulse | Billing Hub | Your Morning Brief |
| Grants | owner, gm, office, all | office, all | all |
| Needs | CO / estimate / invoice approvals | checks to allocate, ready-to-invoice, expiring COI | blocked jobs, checks owed, crew short |
| Metrics | AR 30+, unbilled complete, stuck, crews out | ready to invoice, checks to allocate, AR 30+, COIs expiring | stops today, blocked, checks owed, crews out |
| Queue | Exceptions | Billing queue | Route today |

Adding a role = adding one entry. Don't branch the components.

---

## 6. Phone specifics

- **Needs you today is the entire first screen.** Greeting, one sentence, then cards.
  Nothing above it but the top bar.
- Primary actions `.btn-lg .btn-block`. Queue rows are already 56px. Drawer nav items 44px.
- `.tabbar`: Home · Search · Alerts · Tools. Badges allowed on Alerts.
- Safe-area padding is in the CSS; don't add your own.
- **No nested scrollers** — `.scroll-rows` releases itself on phone.
- Every card action is a **deep link to the exact job or invoice**, not the tool's landing
  page. Two taps, not eight. Biggest single win on mobile.

---

## 7. Build order

1. Shell — `.app` + `.rail` + drawer + `.tabbar`, with the tool config above.
2. `GET /hub` returning `HubPayload`; render the whole screen from it.
3. Needs-you query per role. Ship with the three roles hardcoded, then move to Admin.
4. Badges + refresh interval.
5. Pinned (write path), then Activity as the shared audit feed.
6. Admin: per-user home tool and tool grants.
