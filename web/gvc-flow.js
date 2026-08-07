/**
 * GvcFlow — money-spine Path strip across portal tools.
 * Mount: GvcFlow.mount(el, "estimate")  // current step id
 * Open Takeoff app: GvcFlow.takeoffAppUrl()
 */
(function (global) {
  "use strict";

  var STEPS = [
    { id: "hub", label: "Hub", href: "/" },
    { id: "takeoff", label: "Takeoff", href: "/ui/takeoff" },
    { id: "estimate", label: "Estimate", href: "/ui/estimate" },
    { id: "jobstart", label: "Job Start", href: "/ui/jobstart" },
    { id: "jobcheck", label: "Job Check", href: "/ui/jobcheck" },
    { id: "billing", label: "Billing", href: "/ui/billing" },
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

  function mount(el, currentId) {
    if (!el) return;
    var cur = String(currentId || "");
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

  global.GvcFlow = {
    STEPS: STEPS,
    TAKEOFF_APP: TAKEOFF_APP,
    takeoffAppUrl: takeoffAppUrl,
    mount: mount,
  };
})(typeof window !== "undefined" ? window : globalThis);
