/**
 * GvcFlow — money-spine Path strip across portal tools.
 * Mount: GvcFlow.mount(el, "estimate")  // current step id
 * Open Takeoff app: GvcFlow.takeoffAppUrl()
 *
 * Host classes:
 *   .gvc-path  → forms pack strip (gvc-forms.css) — nested under .gvc-topbar
 *   .path      → redesign track (gvc-ui.css) — hub/takeoff shell
 *   .gvc-flow  → legacy strip (gvc.css) — still used on unconverted pages
 */
(function (global) {
  "use strict";

  var STEPS = [
    { id: "hub", label: "Hub", href: "/" },
    { id: "takeoff", label: "Takeoff", href: "/ui/takeoff" },
    { id: "estimate", label: "Estimate", href: "/ui/estimate" },
    { id: "jobstart", label: "Job Start", href: "/ui/jobstart" },
    { id: "jobcheck", label: "Job Check", href: "/ui/jobcheck" },
    { id: "change_order", label: "CO", href: "/ui/change-order" },
    { id: "billing", label: "Billing", href: "/ui/billing" },
    { id: "invoice", label: "Invoice", href: "/ui/invoice" },
    { id: "check", label: "Check", href: "/ui/check" },
  ];

  var TAKEOFF_APP = "https://gvctakeoff.netlify.app/v2.html";

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function takeoffAppUrl() {
    try {
      var u = new URL(TAKEOFF_APP);
      u.searchParams.set("return", global.location.origin + "/");
      u.searchParams.set("from", "portal");
      return u.toString();
    } catch (_) {
      return TAKEOFF_APP;
    }
  }

  function mountLegacy(el, cur) {
    if (!/\bgvc-flow\b/.test(el.className || "")) {
      el.className = (el.className ? el.className + " " : "") + "gvc-flow";
    }
    el.setAttribute("aria-label", "Money path — jump between tools");
    var parts = [];
    for (var i = 0; i < STEPS.length; i++) {
      var s = STEPS[i];
      if (i) {
        parts.push('<span class="gvc-flow__sep" aria-hidden="true">›</span>');
      }
      if (s.id === cur) {
        parts.push(
          '<span class="gvc-flow__step is-current" aria-current="page">' +
            esc(s.label) +
            "</span>"
        );
      } else {
        parts.push(
          '<a class="gvc-flow__step" href="' +
            esc(s.href) +
            '">' +
            esc(s.label) +
            "</a>"
        );
      }
    }
    el.innerHTML =
      '<span class="gvc-flow__label">Path</span>' +
      '<div class="gvc-flow__track">' +
      parts.join("") +
      "</div>";
  }

  function mountPath(el, cur) {
    if (!/\bpath\b/.test(el.className || "")) {
      el.className = (el.className ? el.className + " " : "") + "path";
    }
    el.setAttribute("aria-label", "Money path — jump between tools");
    var curIdx = -1;
    for (var j = 0; j < STEPS.length; j++) {
      if (STEPS[j].id === cur) {
        curIdx = j;
        break;
      }
    }
    var parts = [];
    for (var i = 0; i < STEPS.length; i++) {
      var s = STEPS[i];
      var cls = "path__step";
      if (s.id === cur) cls += " is-here";
      else if (curIdx >= 0 && i < curIdx) cls += " is-done";
      if (s.id === cur) {
        parts.push(
          '<span class="' +
            cls +
            '" aria-current="page">' +
            esc(s.label) +
            "</span>"
        );
      } else {
        parts.push(
          '<a class="' + cls + '" href="' + esc(s.href) + '">' + esc(s.label) + "</a>"
        );
      }
    }
    el.innerHTML = parts.join("");
  }

  function mountFormsPath(el, cur) {
    if (!/\bgvc-path\b/.test(el.className || "")) {
      el.className = (el.className ? el.className + " " : "") + "gvc-path";
    }
    el.setAttribute("aria-label", "Money path — jump between tools");
    var parts = [];
    for (var i = 0; i < STEPS.length; i++) {
      var s = STEPS[i];
      var here = s.id === cur;
      var cls = "gvc-path-step" + (here ? " is-here" : "");
      if (here) {
        parts.push(
          '<span class="' + cls + '" aria-current="page">' + esc(s.label) + "</span>"
        );
      } else {
        parts.push(
          '<a class="' + cls + '" href="' + esc(s.href) + '">' + esc(s.label) + "</a>"
        );
      }
    }
    el.innerHTML = '<div class="gvc-path-row">' + parts.join("") + "</div>";
  }

  function mount(el, currentId) {
    if (!el) return;
    var cur = String(currentId || "");
    if (/\bgvc-path\b/.test(el.className || "")) {
      mountFormsPath(el, cur);
      return;
    }
    if (/\bpath\b/.test(el.className || "")) {
      mountPath(el, cur);
      return;
    }
    mountLegacy(el, cur);
  }

  global.GvcFlow = {
    STEPS: STEPS,
    TAKEOFF_APP: TAKEOFF_APP,
    takeoffAppUrl: takeoffAppUrl,
    mount: mount,
  };
})(typeof window !== "undefined" ? window : globalThis);
