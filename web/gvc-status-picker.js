/**
 * Job Check status picker — grouped searchable one-value-per-field control.
 * Markup/classes from docs/redesign/Status Picker Handoff.md (copy, don't invent).
 *
 * Closed: .card.card-flush.sp → .row / .kicker / gold .chip / Change
 * Open: search .input → Next up .chip-next → .sp__groups
 * Never tint chips with Monday hex — gold=selected, green=next, neutral=else.
 * Write-back stays with the page Save bar (onChange only).
 */
(function (global) {
  "use strict";

  /**
   * Phase groups in workflow order. Keys = Job Check fieldKey (board:id).
   * Order drives group display AND "Next up" (next N in the flattened list).
   */
  const STATUS_GROUPS = {
    "ops:status": [
      ["Pipeline", [
        "Upcoming", "Estimating", "Draft Estimate", "Sent for Takeoff",
        "Site Takeoff Needed", "First Priority", "Second Priority",
      ]],
      ["Materials", [
        "Stocking", "GVC Material D.O.", "Send Material Order", "Ready to Hang",
      ]],
      ["Framing", [
        "Framing", "Framing Track Layout", "Framing Studs", "Framing Blocking",
        "Framing Punch out", "Insulation Install",
      ]],
      ["Hang", ["Hanging", "Pre-Rock", "Hold for Heat"]],
      ["Finish", ["Scrapping", "Finishing", "Fire Tape", "Touch up - Sand"]],
      ["Ceilings & doors", ["ACT", "Installing doors"]],
      ["Service", ["Touch Up/Service", "Punchout List", "Stuck"]],
      ["Closeout", ["Ready to Invoice", "Complete"]],
      ["Not a job stage", ["Meeting", "Admin", "Personal Item", "OOO-Vacation"]],
    ],
    "ops:color_mm1hmwdm": [
      ["Estimating", [
        "Ops Team", "Site Take-off Needed", "Onsite - Takeoff", "Estimate Needed",
      ]],
      ["Materials", ["Order Supplies", "Delivery Scheduled"]],
      ["Framing", [
        "Layout/RC1", "Track Layout", "Studs", "Blocking", "Framing Punch Out",
      ]],
      ["Hang", [
        "Hang Scheduled", "Hang Started", "Hang < 50%", "Hang > 50%", "Hang Complete",
      ]],
      ["Scrap", [
        "Scrap Scheduled", "Scrap Started", "Scrap Complete", "Ready for finisher",
      ]],
      ["Finish", [
        "Tape", "Bed/2nd Coat", "3rd/Skim Coat", "Skim/Skimskim", "Sand",
        "Clean Out/Final Check",
      ]],
      ["Paint", [
        "Paint Scheduled", "Prime Coat", "1st Coat", "2nd Coat (Paint)", "Paint Complete",
      ]],
      ["ACT & tile", ["ACT Scheduled", "Grid Layout", "Grid Install", "Tile Install"]],
      ["Service", ["Touch Up Scheduled", "Touch Up Started", "Service Scheduled"]],
      ["Billing", ["Invoice Approved to Send"]],
      ["Other", ["Personal"]],
    ],
    "ops:color_mm1hrm6z": [
      ["Status", [
        "Clear", "Blocked", "Hold for Heat", "Waiting on GC",
        "Waiting on Materials", "Jordan",
      ]],
    ],
  };

  /** Prefer these as "Next up" regardless of workflow position. */
  const QUICK = {
    "ops:color_mm1hrm6z": ["Blocked", "Waiting on GC", "Waiting on Materials"],
  };

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function flatConfigured(key) {
    const groups = STATUS_GROUPS[key];
    if (!groups) return [];
    return groups.flatMap(([, items]) => items);
  }

  /**
   * Merge configured groups with live Monday labels.
   * Returns [{name, items: string[]}] — only labels that exist on the board.
   * Unknown live labels land in a trailing Other group.
   */
  function groupsFor(key, liveLabels) {
    const live = (liveLabels || []).map((l) =>
      typeof l === "string" ? l : (l && l.label) || ""
    ).filter(Boolean);
    const liveSet = new Set(live);
    const configured = STATUS_GROUPS[key];
    const used = new Set();
    const out = [];

    if (configured) {
      configured.forEach(([name, items]) => {
        const hit = items.filter((v) => liveSet.has(v));
        hit.forEach((v) => used.add(v));
        if (hit.length) out.push({ name: name, items: hit });
      });
    }

    const missing = live.filter((v) => !used.has(v));
    if (missing.length) {
      if (!configured) {
        out.push({ name: "All statuses", items: missing });
      } else {
        out.push({ name: "Other", items: missing });
        if (typeof console !== "undefined" && console.warn) {
          console.warn("Ungrouped status values for " + key + ":", missing);
        }
      }
    }
    return out;
  }

  function suggestionsFor(key, current, liveLabels) {
    const cur = current == null ? "" : String(current);
    if (QUICK[key]) {
      return QUICK[key].filter((v) => v !== cur && (!liveLabels || liveLabels.includes(v)));
    }
    const groups = groupsFor(key, liveLabels && liveLabels.length
      ? liveLabels
      : flatConfigured(key));
    const list = groups.flatMap((g) => g.items);
    const i = list.indexOf(cur);
    if (i < 0) return list.slice(0, 3);
    return list.slice(i + 1, i + 4);
  }

  function chipHtml(value, { active, next }) {
    const cls = ["chip"];
    if (active) cls.push("is-active");
    if (next) cls.push("chip-next");
    return (
      `<button type="button" class="${cls.join(" ")}" data-value="${esc(value)}" ` +
      `aria-pressed="${active ? "true" : "false"}">${esc(value)}</button>`
    );
  }

  /**
   * @param {{
   *   key: string, label: string, value: string|null,
   *   labels: (string|{label:string})[],
   *   writable?: boolean,
   *   hint?: string,
   *   onChange?: (value: string|null) => void
   * }} cfg
   */
  function mount(cfg) {
    const key = cfg.key;
    const label = cfg.label || key;
    const writable = cfg.writable !== false;
    const liveRows = cfg.labels || [];
    const liveLabels = liveRows.map((l) =>
      typeof l === "string" ? l : (l && l.label) || ""
    ).filter(Boolean);
    let value = cfg.value == null || cfg.value === "" ? "" : String(cfg.value);

    const sec = document.createElement("section");
    sec.className = "card card-flush sp";
    sec.dataset.field = key;
    sec.setAttribute("aria-label", label);

    function renderClosed() {
      const has = !!value;
      const currentHtml = has
        ? `<button type="button" class="chip is-active" data-clear aria-pressed="true">${esc(value)}</button>`
        : `<span class="faint" style="font-size:var(--text-xs)">(not set)</span>`;
      const hint = cfg.hint
        ? `<em class="faint" style="font-size:var(--text-xs)">${esc(cfg.hint)}</em>`
        : "";
      sec.innerHTML =
        `<div class="row" style="border-bottom:0">` +
        `<div class="row__main">` +
        `<span class="kicker">${esc(label)}</span>` +
        `<span class="cluster">${currentHtml}${hint}</span>` +
        `</div>` +
        (writable
          ? `<button type="button" class="btn btn-sm row__end" data-toggle>Change</button>`
          : "") +
        `</div>` +
        `<div class="sp__panel card-foot stack" hidden aria-hidden="true"></div>`;
    }

    function renderPanel() {
      const panel = sec.querySelector(".sp__panel");
      if (!panel) return;
      const groups = groupsFor(key, liveLabels);
      const next = suggestionsFor(key, value, liveLabels);
      const sugHtml = next.length
        ? `<div class="stack" style="gap:var(--space-2)" data-suggestions>` +
          `<span class="kicker kicker-sm">Next up</span>` +
          `<div class="cluster">${next.map((v) => chipHtml(v, { next: true, active: false })).join("")}</div>` +
          `</div>`
        : `<div class="stack" style="gap:var(--space-2)" data-suggestions hidden></div>`;

      const groupsHtml = groups.map((g) => {
        const holds = value && g.items.includes(value);
        const open = holds ? "true" : "false";
        const chips = g.items.map((v) =>
          chipHtml(v, { active: v === value, next: false })
        ).join("");
        return (
          `<div class="sp__group" data-open="${open}">` +
          `<button type="button" class="sp__ghead" data-group aria-expanded="${open}">` +
          `<span class="sp__gname">${esc(g.name)}</span>` +
          (holds ? `<i class="sp__gdot" aria-hidden="true"></i>` : "") +
          `<span class="mono faint" style="font-size:var(--text-xs)">${g.items.length}</span>` +
          `<span class="faint sp__gcaret">${open === "true" ? "–" : "+"}</span>` +
          `</button>` +
          `<div class="cluster sp__opts">${chips}</div>` +
          `</div>`
        );
      }).join("");

      panel.innerHTML =
        `<input class="input" type="search" placeholder="Type to find a status" autocomplete="off" />` +
        sugHtml +
        `<div class="sp__groups">${groupsHtml}</div>`;
    }

    function setValue(next) {
      const n = next == null ? "" : String(next);
      value = n;
      if (typeof cfg.onChange === "function") {
        cfg.onChange(n === "" ? null : n);
      }
      const open = !sec.querySelector(".sp__panel")?.hidden;
      renderClosed();
      if (open) {
        openPanel();
      }
    }

    function openPanel() {
      renderPanel();
      const panel = sec.querySelector(".sp__panel");
      panel.hidden = false;
      panel.setAttribute("aria-hidden", "false");
      const toggle = sec.querySelector("[data-toggle]");
      if (toggle) toggle.textContent = "Done";
      const search = panel.querySelector(".input");
      if (search) search.focus();
    }

    function closePanel() {
      const panel = sec.querySelector(".sp__panel");
      if (!panel) return;
      panel.hidden = true;
      panel.setAttribute("aria-hidden", "true");
      panel.innerHTML = "";
      const toggle = sec.querySelector("[data-toggle]");
      if (toggle) toggle.textContent = "Change";
    }

    function togglePanel() {
      const panel = sec.querySelector(".sp__panel");
      if (!panel || panel.hidden) openPanel();
      else closePanel();
    }

    sec.addEventListener("click", (e) => {
      if (!writable && !e.target.closest("[data-clear]")) return;

      if (e.target.closest("[data-toggle]")) {
        e.preventDefault();
        togglePanel();
        return;
      }

      const ghead = e.target.closest("[data-group]");
      if (ghead) {
        e.preventDefault();
        const g = ghead.closest(".sp__group");
        const nextOpen = g.dataset.open === "true" ? "false" : "true";
        g.dataset.open = nextOpen;
        ghead.setAttribute("aria-expanded", nextOpen);
        const caret = ghead.querySelector(".sp__gcaret");
        if (caret) caret.textContent = nextOpen === "true" ? "–" : "+";
        return;
      }

      const clearBtn = e.target.closest("[data-clear]");
      if (clearBtn) {
        e.preventDefault();
        setValue("");
        closePanel();
        return;
      }

      const opt = e.target.closest("[data-value]");
      if (opt && sec.contains(opt)) {
        e.preventDefault();
        const v = opt.getAttribute("data-value") || "";
        setValue(v === value ? "" : v);
        closePanel();
      }
    });

    sec.addEventListener("input", (e) => {
      const input = e.target.closest(".sp__panel .input");
      if (!input || !sec.contains(input)) return;
      const q = input.value.trim().toLowerCase();
      const sug = sec.querySelector("[data-suggestions]");
      if (sug) sug.hidden = !!q;
      sec.querySelectorAll(".sp__group").forEach((g) => {
        let shown = 0;
        g.querySelectorAll(".chip[data-value]").forEach((chip) => {
          const hit = !q || chip.textContent.toLowerCase().includes(q);
          chip.hidden = !hit;
          if (hit) shown++;
        });
        g.hidden = shown === 0;
        if (q) {
          g.dataset.open = "true";
          const head = g.querySelector("[data-group]");
          if (head) {
            head.setAttribute("aria-expanded", "true");
            const caret = head.querySelector(".sp__gcaret");
            if (caret) caret.textContent = "–";
          }
        }
        const count = g.querySelector(".mono");
        if (count) count.textContent = String(shown);
      });
    });

    renderClosed();
    return {
      el: sec,
      getValue: () => value || null,
      setValue: setValue,
      close: closePanel,
    };
  }

  global.GvcStatusPicker = {
    STATUS_GROUPS: STATUS_GROUPS,
    QUICK: QUICK,
    flatConfigured: flatConfigured,
    groupsFor: groupsFor,
    suggestionsFor: suggestionsFor,
    mount: mount,
  };
})(typeof window !== "undefined" ? window : globalThis);
