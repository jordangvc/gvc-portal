/**
 * GvcFieldJump — click a validation error → land on the field to fix.
 * Used by money forms, Job Start, COI, and any status banner with [data-jump].
 *
 * Gap objects: { message, focus?: id|selector, step?: number, go?: fn }
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

  function resolve(target) {
    if (!target) return null;
    if (typeof target !== "string") return target;
    if (target.charAt(0) === "#" || target.charAt(0) === "." || target.charAt(0) === "[") {
      return document.querySelector(target);
    }
    return (
      document.getElementById(target) ||
      document.querySelector('[name="' + target.replace(/"/g, '\\"') + '"]') ||
      document.querySelector('[data-field="' + target.replace(/"/g, '\\"') + '"]') ||
      document.querySelector('[data-col="' + target.replace(/"/g, '\\"') + '"]')
    );
  }

  function focus(target, opts) {
    opts = opts || {};
    var el = resolve(target);
    if (!el) return false;
    try {
      if (typeof opts.go === "function") opts.go();
      el.scrollIntoView({ behavior: "smooth", block: "center" });
    } catch (_) {}
    try {
      var focusable = el;
      if (el.matches && !el.matches("input,textarea,select,button,[tabindex]")) {
        focusable =
          el.querySelector("input:not([type=hidden]),textarea,select,button,[tabindex]") || el;
      }
      if (focusable && focusable.focus) focusable.focus({ preventScroll: true });
    } catch (_) {}
    try {
      el.classList.add("is-jump-target");
      window.setTimeout(function () {
        el.classList.remove("is-jump-target");
      }, 1600);
    } catch (_) {}
    return true;
  }

  function link(label, target, extraClass) {
    return (
      '<button type="button" class="field-jump' +
      (extraClass ? " " + extraClass : "") +
      '" data-jump="' +
      esc(target) +
      '">' +
      esc(label) +
      "</button>"
    );
  }

  function list(items) {
    items = items || [];
    if (!items.length) return "";
    return (
      '<ul class="field-jump-list">' +
      items
        .map(function (it) {
          var label = it.label || it.message || "Fix this field";
          var target = it.focus || it.key || "";
          if (!target) return "<li>" + esc(label) + "</li>";
          return "<li>" + link(label, target) + "</li>";
        })
        .join("") +
      "</ul>"
    );
  }

  function banner(message, target) {
    if (!target) return esc(message);
    return (
      '<strong>Needs a fix — tap to go there:</strong> ' +
      link(message, target, "field-jump--banner")
    );
  }

  function onClick(e) {
    var btn = e.target.closest("[data-jump]");
    if (!btn || !document.body.contains(btn)) return;
    e.preventDefault();
    var target = btn.getAttribute("data-jump");
    var step = btn.getAttribute("data-jump-step");
    var go = null;
    if (step != null && step !== "" && global.GvcFormStages && global.__gvcFormStagesApi) {
      go = function () {
        try {
          global.__gvcFormStagesApi.go(+step);
        } catch (_) {}
      };
    } else if (btn._gvcGo) {
      go = btn._gvcGo;
    }
    focus(target, { go: go });
  }

  function bind(root) {
    var el = root || document;
    el.addEventListener("click", onClick);
  }

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", function () {
        bind(document);
      });
    } else {
      bind(document);
    }
  }

  global.GvcFieldJump = {
    focus: focus,
    resolve: resolve,
    link: link,
    list: list,
    banner: banner,
    bind: bind,
  };
})(typeof window !== "undefined" ? window : globalThis);
