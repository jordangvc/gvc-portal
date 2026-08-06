/**
 * GVC portal theme — system | light | dark.
 *
 * Storage key: gvc-theme. Values: "system" | "light" | "dark".
 * Default: system (follow prefers-color-scheme).
 * Sets html[data-theme="light"|"dark"] so gvc.css emerald tokens remap.
 *
 * Boot without FOUC: call GvcTheme.boot() from an inline <head> script
 * (or load this file with defer=false before first paint). Toggle UI:
 *   GvcTheme.mount(buttonOrContainer)
 */
(function (global) {
  "use strict";

  var KEY = "gvc-theme";
  var MODES = { system: true, light: true, dark: true };

  function readStored() {
    try {
      var v = window.localStorage.getItem(KEY);
      if (v && MODES[v]) return v;
    } catch (e) { /* private mode */ }
    return "system";
  }

  function writeStored(mode) {
    try {
      window.localStorage.setItem(KEY, mode);
    } catch (e) { /* no-op */ }
  }

  function systemPrefersDark() {
    try {
      return !!(window.matchMedia &&
        window.matchMedia("(prefers-color-scheme: dark)").matches);
    } catch (e) {
      return false;
    }
  }

  function resolve(mode) {
    if (mode === "light" || mode === "dark") return mode;
    return systemPrefersDark() ? "dark" : "light";
  }

  function apply(mode) {
    var m = MODES[mode] ? mode : "system";
    var resolved = resolve(m);
    var root = document.documentElement;
    root.setAttribute("data-theme", resolved);
    root.setAttribute("data-theme-mode", m);
    if (!root.getAttribute("data-palette")) {
      root.setAttribute("data-palette", "emerald");
    }
    return { mode: m, theme: resolved };
  }

  function setMode(mode) {
    var m = MODES[mode] ? mode : "system";
    writeStored(m);
    var out = apply(m);
    global.dispatchEvent(new CustomEvent("gvc-theme", { detail: out }));
    return out;
  }

  function cycle() {
    var cur = readStored();
    var next = cur === "system" ? "light" : cur === "light" ? "dark" : "system";
    return setMode(next);
  }

  function label(mode) {
    if (mode === "light") return "Light";
    if (mode === "dark") return "Dark";
    return "Auto";
  }

  function title(mode) {
    if (mode === "light") return "Theme: light (tap for dark)";
    if (mode === "dark") return "Theme: dark (tap for auto)";
    return "Theme: auto / system (tap for light)";
  }

  function paintToggle(el, mode) {
    if (!el) return;
    el.setAttribute("aria-label", title(mode));
    el.title = title(mode);
    el.dataset.themeMode = mode;
    var text = el.querySelector("[data-theme-label]");
    if (text) text.textContent = label(mode);
    else if (!el.dataset.themeIconOnly) el.textContent = label(mode);
  }

  function mount(target) {
    if (!target) return null;
    var el = target;
    if (typeof target === "string") el = document.querySelector(target);
    if (!el) return null;
    if (!el.getAttribute("type") && el.tagName === "BUTTON") {
      el.setAttribute("type", "button");
    }
    paintToggle(el, readStored());
    el.addEventListener("click", function () {
      var out = cycle();
      paintToggle(el, out.mode);
    });
    global.addEventListener("gvc-theme", function (ev) {
      paintToggle(el, (ev.detail && ev.detail.mode) || readStored());
    });
    return el;
  }

  function boot() {
    var out = apply(readStored());
    if (window.matchMedia) {
      try {
        var mq = window.matchMedia("(prefers-color-scheme: dark)");
        var onChange = function () {
          if (readStored() === "system") apply("system");
        };
        if (mq.addEventListener) mq.addEventListener("change", onChange);
        else if (mq.addListener) mq.addListener(onChange);
      } catch (e) { /* old WebView */ }
    }
    return out;
  }

  var api = {
    KEY: KEY,
    boot: boot,
    getMode: readStored,
    setMode: setMode,
    cycle: cycle,
    resolve: resolve,
    mount: mount,
    label: label,
  };

  global.GvcTheme = api;

  /* Auto-boot when loaded as a normal script (inline head boot still preferred). */
  if (document.documentElement) {
    boot();
  }
})(typeof window !== "undefined" ? window : this);
