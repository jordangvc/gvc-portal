/**
 * GvcFormChrome — ONE shared topbar + nested path for generator forms.
 * Markup matches docs/redesign/gvc-forms-reference.html exactly.
 *
 * Mount: GvcFormChrome.mount({ app: "invoice", email: "…" })
 *   app: "estimate" | "change" | "invoice"
 *
 * Requires: gvc-ui.css + gvc-forms.css, GvcFlow (path steps).
 */
(function (global) {
  "use strict";

  var APPS = [
    { id: "estimate", label: "Estimate", href: "/ui/estimate", path: "estimate" },
    { id: "change", label: "Change Order", href: "/ui/change-order", path: "change_order" },
    { id: "invoice", label: "Invoice", href: "/ui/invoice", path: "invoice" },
  ];

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function initials(email) {
    var s = String(email || "").trim();
    if (!s) return "GV";
    var local = s.split("@")[0] || s;
    var parts = local.replace(/[._-]+/g, " ").split(/\s+/).filter(Boolean);
    if (parts.length >= 2) {
      return (parts[0][0] + parts[1][0]).toUpperCase();
    }
    return local.slice(0, 2).toUpperCase();
  }

  function mount(opts) {
    opts = opts || {};
    var app = String(opts.app || "estimate");
    var host = opts.el || document.getElementById("gvc-form-chrome");
    if (!host) return null;

    var appBtns = APPS.map(function (a) {
      var on = a.id === app;
      return (
        '<a class="gvc-app' +
        (on ? " is-on" : "") +
        '" href="' +
        esc(a.href) +
        '"' +
        (on ? ' aria-current="page"' : "") +
        ' data-app="' +
        esc(a.id) +
        '">' +
        esc(a.label) +
        "</a>"
      );
    }).join("");

    var av = esc(initials(opts.email));
    host.innerHTML =
      '<header class="gvc-topbar">' +
      '<div class="gvc-topbar-row">' +
      '<a class="gvc-brand" href="/" style="text-decoration:none;color:inherit">' +
      '<div class="gvc-brand-mark">G</div>' +
      '<div class="gvc-brand-name">Green Valley</div>' +
      "</a>" +
      '<nav class="gvc-appnav" aria-label="Generators">' +
      appBtns +
      "</nav>" +
      '<div class="gvc-topbar-spacer"></div>' +
      '<div class="gvc-saved" id="gvc-saved" hidden>' +
      '<span class="gvc-saved-dot"></span>' +
      "<span id=\"gvc-saved-label\">Autosaved</span>" +
      "</div>" +
      '<button type="button" class="gvc-btn gvc-btn-ghost-dark gvc-btn-sm" id="theme-toggle" data-theme-label ' +
      'style="height:32px;padding:0 12px;border-color:#2c5a45;color:#9db3a6">Auto</button>' +
      '<a class="gvc-avatar" href="/auth/logout" title="Sign out" ' +
      'style="text-decoration:none">' +
      av +
      "</a>" +
      "</div>" +
      '<div class="gvc-path" id="gvc-flow" aria-label="Money path"></div>' +
      "</header>";

    var pathId = "estimate";
    for (var i = 0; i < APPS.length; i++) {
      if (APPS[i].id === app) {
        pathId = APPS[i].path;
        break;
      }
    }
    if (global.GvcFlow && global.GvcFlow.mount) {
      global.GvcFlow.mount(document.getElementById("gvc-flow"), pathId);
    }
    if (global.GvcTheme && global.GvcTheme.mount) {
      global.GvcTheme.mount(document.getElementById("theme-toggle"));
    }
    return host;
  }

  function setSaved(text) {
    var wrap = document.getElementById("gvc-saved");
    var label = document.getElementById("gvc-saved-label");
    if (!wrap) return;
    if (!text) {
      wrap.hidden = true;
      return;
    }
    wrap.hidden = false;
    if (label) label.textContent = text;
  }

  global.GvcFormChrome = {
    APPS: APPS,
    mount: mount,
    setSaved: setSaved,
  };
})(typeof window !== "undefined" ? window : globalThis);
