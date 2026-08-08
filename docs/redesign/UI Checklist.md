# UI checklist — run this before any screen ships

The pack defines what things look like. This defines when a screen is *done*. Run it on
every new page and every page you touch.

---

## 1. The three-second test

Open the screen cold and answer, without reading body copy:

- [ ] **Where am I?** — kicker + title in the `.topbar`, and one `.nav-item.is-active`.
- [ ] **What is this page for?** — one sentence under the title. Not a paragraph.
- [ ] **What do I do next?** — exactly one `.btn-primary` in view.

If any answer takes longer than three seconds, the page fails. It doesn't matter how
good it looks.

## 2. One primary action

**Where green is allowed.** Three places, and nowhere else:

- [ ] **Once in the page flow** — the action that moves the job forward, last in reading
      order. This is the screen's primary.
- [ ] **Once inside a card that owns a decision** — a confirm footer ("Mark sent"), an
      `.empty` state's only action, a banner asking you to choose ("Review and purge").
      One per card. Repeated decision cards (a needs-you queue) each get theirs.
- [ ] Nowhere else. Specifically **never**:
  - in a **topbar** — "Save" and "Update" in the top bar are outlined. A topbar action is
    convenience; the primary lives in the flow.
  - on a **section-level add** — "+ Add", "+ Add floor", "+ Add a run" are `.btn.btn-sm`,
    however many there are. Green on each turns a builder screen into a field of green.
  - in a **row of peer tools** — Email / Copy / PDF / Edit / Reset are all one weight.
    Promoting one implies the others are lesser, which they aren't.
- [ ] **Never twice for one action.** A topbar "Save" plus a footer "Save" is one action
      wearing green twice. The one in flow position keeps it.
- [ ] **One weight per row.** Don't mix `.btn-ghost` into a row of outlined `.btn`s — the
      ghost reads as a text label, not a button. Ghost is for utilities standing alone
      (Collapse all / Expand all), not for a peer in a group.
- [ ] It is the action that moves the job forward — not "Save", unless saving is the
      point of the page.
- [ ] It sits **last in reading order**. If a screen has an irreversible action, everything
      you must check first comes above it.
- [ ] Its label is a verb and an object: "Approve and send to office", "Start measuring",
      "Finish the name". Never "Submit", "OK", "Go".
- [ ] Anything that puts a document in front of a customer says **"Approve to send"** and
      a person presses send.

## 3. No dead ends

For every screen, name the exit:

- [ ] **Back** — a `.btn-icon` in the topbar or a named "Back to X" button. Every screen
      that isn't the home screen has one.
- [ ] **Forward** — the primary action, or an explicit "nothing to do here" empty state
      that offers the action that fills it.
- [ ] **Out** — the rail (desktop) or the tab bar (phone) is always reachable.
- [ ] **Drawers close.** Anything `position: fixed; inset: 0` covers the control that
      opened it. It needs a `.phone-only` close button. This is the most common trap.
- [ ] **Dialogs close** on backdrop click, Escape, and an explicit Cancel.
- [ ] **Blocked states offer a way through.** "Can't reach the plan file" pairs
      *Try again* with *Keep measuring without it*. A validation error that only says no
      is a dead end.

## 4. Four states, every list and screen

- [ ] **Full** — the normal case.
- [ ] **Empty** — `.empty`, naming what goes there and offering the action that fills it.
      Never "No data". Use `.empty-clear` when the empty is *good news* (queue cleared).
- [ ] **Loading** — `.skel` in the shape of the content. Never a spinner for content with
      a known shape. Buttons mid-action take `.is-working` and keep their label.
- [ ] **Error** — `.errorbox`, saying what failed, what it means, and what to do.
- [ ] **Offline** — `.stale` where field use is likely. Warm, not red.
- [ ] **No permission** — `.nav-item.is-locked`: dimmed with an em dash, never hidden.
      People should learn a tool exists and ask for access.

## 5. Consistency

- [ ] Every button is `.btn`. Grep for `<button` without `class="btn` — should be empty
      except `.btn-row`, `.seg`, `.chip`, `.tchip`, `.stepper` and `.disclosure__head`
      children.
- [ ] Every green primary carries the gold inset hairline.
- [ ] Every input is a pill at ≥44px.
- [ ] Every field label is `.kicker`. No sentence-case labels mixed in.
- [ ] No `<select>`. Finite option sets are `.chipset` groups.
- [ ] Grep the file for `#` — no hex codes. Colors come from `var(--color-*)`.
- [ ] No `linear-gradient`. No `opacity` on text or icons.
- [ ] Muted for information, faint for absence. Nothing readable is set in faint.
- [ ] The full radius is on controls only. Rows, cards and panels use `--radius-md/lg`.

## 5b. A page that is mostly empty

An empty-looking page is a content problem, not a spacing one. Before widening margins:

- [ ] **Is the main action buried?** A bridge page's one job is sending you somewhere.
      That destination is a `.handoff__card`, not a button in a row of three.
- [ ] **Is something said twice?** A breadcrumb strip *and* a numbered prose list of the
      same steps is one thing written twice. Make it a `.path` track once.
- [ ] **Is there real state the page could show?** A queue, a count, what's waiting.
      A page that knows three takeoffs are staged should say so rather than describing
      the process in the abstract.
- [ ] Do **not** fill space with placeholder cards, stat tiles nobody asked for, or
      restated copy. Prose describing what a page does is usually the page failing to
      just do it.

## 6. Repetition

- [ ] If a toolbar, confirm bar or header appears **more than twice** on one screen with
      only its content changing, it is **one component with a picker** — not N copies.
      Seven output toolbars became one Outputs card with a chip row.
- [ ] If two screens do the same job with different markup, one of them is wrong.

## 7. One number, one source

- [ ] Every count that appears twice comes from **one** query. Walk the screen and list
      every figure; if two disagree, that is a bug you are shipping.
- [ ] Section headers that count children ("Detail — 6 sections") are computed, not typed.
- [ ] **Arithmetic is derived, never typed.** A subtotal, a board count, a waste-adjusted
      figure — compute it from the rows on screen. A drywall contractor spots 18 where the
      math says 14 instantly, and once one number is wrong none of them are trusted.
- [ ] Rows excluded by validation are excluded from the total, and the total says so.
- [ ] A running total on a partially-filled screen is labelled as running ("14 bd so far"),
      never as a finished figure.
- [ ] **A total names what it totals.** "2 boxes + 14 pcs" over a list of seven bead lines
      says nothing about which lines it covers. If a figure counts a subset (no-coat only)
      and another counts everything, show both as separate rows and label each.
- [ ] **Units that don't add don't get summed.** Pieces, boxes, rolls and boards are
      different things; a total that silently mixes them is wrong even when the number is
      right.
- [ ] **Check every card, not just the one you edited.** These bugs travel in families —
      a fix on one card usually means its sibling has the same defect.

## 8. Phone — 390px, the real target

- [ ] Load the screen at 390×780. ~90% of use is a phone in a truck.
- [ ] Nothing clips horizontally. `min-width: 0` on every flex/grid child that holds a
      job name; `overflow-wrap: anywhere` on the name itself.
- [ ] No nested scrollers. `.scroll-rows` / `.scroll-tall` release themselves — don't add
      your own `max-height`.
- [ ] Every target ≥44px, primary actions 48px.
- [ ] Filter groups are one swipeable row each, not a wrapping wall.
- [ ] Card actions **deep-link to the exact job or invoice**, never the tool's landing
      page. Two taps, not eight.

## 9. Data honesty

- [ ] Job names follow `[Street], [City], [ST] [ZIP] | [Builder] | [Job Title]`.
- [ ] A record missing a ZIP, builder or title shows a `.tag-gold` saying so, and its
      primary action becomes **Finish the name**. Ask, don't guess.
- [ ] Test records, recovered autosaves and untitled drafts are grouped separately with
      `.rowcard-junk` — never sorted in among real jobs.

---

## Adding a new page

1. Copy the shell from `gvc-shell-demo.html`. Don't hand-roll a rail or a top bar.
2. Find the closest existing screen and copy its patterns —
   `gvc-takeoffs-reference.html` for a list, `gvc-measuring-reference.html` for a form,
   `gvc-review-reference.html` for a summary-and-send.
3. Write the markup with existing classes. **If you are writing CSS, stop** — either the
   class exists and you missed it, or the pattern is new and belongs in `gvc-ui.css`
   with a name, not inline on one page.
4. Run this checklist.

## When you genuinely need a new component

Rare. Before adding to `gvc-ui.css`:

- [ ] It appears on **two or more** screens, or it will.
- [ ] No existing class does the job with different content.
- [ ] It uses only `var(--*)` tokens — no new hex, no new px scale.
- [ ] It carries its own empty/loading/error handling if it holds content.
- [ ] It is documented in `GVC Design Style.md` in the same voice as its neighbors.
- [ ] It is added to `gvc-ui.css` — never to a page.
