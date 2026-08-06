/**
 * GVC Command — chip / segmented helpers (docs/GVC-COMMAND-STYLE.md).
 * Finite option sets render as visible pills; a hidden <input name=…> keeps
 * existing val()/setField()/FormData paths working.
 *
 * Usage:
 *   GvcChips.replaceSelect(document.querySelector('select[name=project_type]'));
 *   GvcChips.sync('project_type');           // after setField
 *   GvcChips.mount({ name, options, value, label, segmented: true });
 */
(function (global) {
  "use strict";

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function cssEscape(s) {
    if (typeof CSS !== "undefined" && CSS.escape) return CSS.escape(s);
    return String(s).replace(/[^a-zA-Z0-9_-]/g, "\\$&");
  }

  function paint(root, value) {
    const v = String(value == null ? "" : value);
    root.querySelectorAll("[data-value]").forEach((btn) => {
      const on = String(btn.getAttribute("data-value")) === v;
      btn.classList.toggle("is-active", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function bind(root, input, onChange) {
    root.addEventListener("click", (ev) => {
      const btn = ev.target.closest("[data-value]");
      if (!btn || !root.contains(btn) || btn.disabled) return;
      ev.preventDefault();
      const next = btn.getAttribute("data-value");
      if (input) input.value = next == null ? "" : next;
      paint(root, input ? input.value : next);
      if (typeof onChange === "function") onChange(input ? input.value : next, btn);
      input && input.dispatchEvent(new Event("change", { bubbles: true }));
    });
  }

  function optionsHtml(options, value, cls) {
    const v = String(value == null ? "" : value);
    return options
      .map((opt) => {
        const val = opt.value == null ? "" : String(opt.value);
        const label = opt.label != null ? opt.label : val;
        const on = val === v;
        return (
          `<button type="button" class="${cls}${on ? " is-active" : ""}" ` +
          `data-value="${esc(val)}" aria-pressed="${on ? "true" : "false"}">` +
          `${esc(label)}</button>`
        );
      })
      .join("");
  }

  /**
   * @param {{name:string, options:{value:string,label?:string}[], value?:string,
   *          label?:string, segmented?:boolean, onChange?:Function}} cfg
   * @returns {HTMLElement} chipset/segmented root
   */
  function mount(cfg) {
    const name = cfg.name;
    const options = cfg.options || [];
    const value = cfg.value != null ? cfg.value : (options[0] && options[0].value) || "";
    const segmented = !!cfg.segmented;
    const wrap = document.createElement("div");
    wrap.className = segmented ? "segmented" : "chipset";
    wrap.setAttribute("data-chipset", name);
    if (!segmented && cfg.label) {
      wrap.innerHTML =
        `<span class="chipset__label">${esc(cfg.label)}</span>` +
        `<div class="chipset__opts"></div>`;
      wrap.querySelector(".chipset__opts").innerHTML = optionsHtml(
        options,
        value,
        "chip"
      );
    } else if (!segmented) {
      wrap.innerHTML = `<div class="chipset__opts">${optionsHtml(options, value, "chip")}</div>`;
    } else {
      wrap.innerHTML = optionsHtml(options, value, "");
    }
    let input = cfg.input || (name && document.querySelector(`[name="${cssEscape(name)}"]`));
    if (!input && name) {
      input = document.createElement("input");
      input.type = "hidden";
      input.name = name;
      if (cfg.id) input.id = cfg.id;
      wrap.appendChild(input);
    }
    if (input) input.value = value;
    bind(wrap, input, cfg.onChange);
    paint(wrap, value);
    return wrap;
  }

  /**
   * Replace a <select> in place with chips (or segmented if ≤3 options and
   * cfg.segmented !== false for 2-option sets).
   */
  function replaceSelect(selectEl, cfg) {
    cfg = cfg || {};
    if (!selectEl || selectEl.tagName !== "SELECT") return null;
    const name = selectEl.getAttribute("name") || cfg.name || "";
    const id = selectEl.id || "";
    const options = Array.from(selectEl.options).map((o) => ({
      value: o.value,
      label: (o.textContent || "").trim(),
    }));
    // Drop empty-value placeholders ("", "—", "Select…") when other options exist.
    // Keep intentional empties with real labels (e.g. Outcome "any").
    const meaningful = options.filter((o) => {
      if (o.value !== "") return true;
      if (options.length === 1) return true;
      const lab = (o.label || "").trim();
      if (!lab) return false;
      if (lab === "—" || lab === "-" || lab === "–") return false;
      if (/^select\b/i.test(lab) || /^choose\b/i.test(lab)) return false;
      return true;
    });
    const useOpts = meaningful.length ? meaningful : options;
    const value = selectEl.value;
    const segmented =
      cfg.segmented != null ? !!cfg.segmented : useOpts.length > 0 && useOpts.length <= 3;

    const hidden = document.createElement("input");
    hidden.type = "hidden";
    if (name) hidden.name = name;
    if (id) hidden.id = id;
    hidden.value = value;
    if (selectEl.required) hidden.required = true;
    if (selectEl.disabled) hidden.disabled = true;

    const root = mount({
      name: name,
      id: id,
      options: useOpts,
      value: value,
      label: cfg.label,
      segmented: segmented,
      input: hidden,
      onChange: cfg.onChange,
    });
    // Prefer putting the hidden inside the form near the chips
    root.appendChild(hidden);

    const parent = selectEl.parentNode;
    parent.insertBefore(root, selectEl);
    selectEl.remove();
    return root;
  }

  /** Sync active chip after programmatic setField/setVal. */
  function sync(nameOrEl) {
    let input =
      typeof nameOrEl === "string"
        ? document.querySelector(`[name="${cssEscape(nameOrEl)}"]`)
        : nameOrEl;
    if (!input) return;
    const name = input.getAttribute("name");
    const root =
      (name && document.querySelector(`[data-chipset="${cssEscape(name)}"]`)) ||
      input.closest("[data-chipset]");
    if (root) paint(root, input.value);
  }

  /** Build status chips from {label, color?}[] — for Job Check / Job Start. */
  function statusChipset(cfg) {
    const key = cfg.key || cfg.name || "";
    const rawLabels = cfg.labels || [];
    const labels = rawLabels.map((lab) =>
      typeof lab === "string" ? lab : (lab && lab.label) || ""
    ).filter(Boolean);
    const colors = cfg.colors || {};
    // Allow [{label,hex}] without a separate colors map
    rawLabels.forEach((lab) => {
      if (lab && typeof lab === "object" && lab.label && lab.hex && !colors[lab.label]) {
        colors[lab.label] = lab.hex;
      }
    });
    const value = cfg.value == null ? "" : String(cfg.value);
    const wrap = document.createElement("div");
    wrap.className = "chipset";
    wrap.setAttribute("data-chipset", key);
    const opts = document.createElement("div");
    opts.className = "chipset__opts";
    const all = [{ value: "", label: cfg.emptyLabel || "(not set)" }].concat(
      labels.map((lab) => ({ value: lab, label: lab }))
    );
    opts.innerHTML = all
      .map((o) => {
        const on = String(o.value) === value;
        const hex = o.value && colors[o.value] ? colors[o.value] : "";
        const style = hex
          ? ` style="border-color:${esc(hex)};${on ? `background:${esc(hex)}22;` : ""}"`
          : "";
        return (
          `<button type="button" class="chip${on ? " is-active" : ""}" ` +
          `data-value="${esc(o.value)}" aria-pressed="${on ? "true" : "false"}"${style}>` +
          `${esc(o.label)}</button>`
        );
      })
      .join("");
    wrap.appendChild(opts);
    let input = cfg.input;
    if (!input) {
      input = document.createElement("input");
      input.type = "hidden";
      if (cfg.name) input.name = cfg.name;
      if (cfg.dataset) {
        Object.keys(cfg.dataset).forEach((k) => input.setAttribute("data-" + k, cfg.dataset[k]));
      }
      wrap.appendChild(input);
    }
    input.value = value;
    bind(wrap, input, cfg.onChange);
    return wrap;
  }

  global.GvcChips = {
    mount: mount,
    replaceSelect: replaceSelect,
    sync: sync,
    paint: paint,
    statusChipset: statusChipset,
  };
})(typeof window !== "undefined" ? window : globalThis);
