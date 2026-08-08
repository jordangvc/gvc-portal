/**
 * GvcFormStages — stage rail + Continue/Back/Accept for generator forms.
 * Markup classes from gvc-forms.css. Does not replace page business logic —
 * pages pass validators and accept/preview hooks.
 *
 * GvcFormStages.mount({
 *   stages: ["Find","Who","Job & terms","Lines","Review"],
 *   panels: [NodeList or ids for stages 0..4],
 *   acceptedEl: element shown after accept,
 *   barLabel, getTotals: () => ({ total, sub }),
 *   validateStep: (i) => null | { message, step },
 *   onEnterReview: async () => {},  // usually dry-run preview
 *   onAccept: async () => {},
 *   onStartAnother: () => {},
 * });
 */
(function (global) {
  "use strict";

  function $(id) {
    return document.getElementById(id);
  }

  function mount(cfg) {
    cfg = cfg || {};
    var labels = cfg.stages || ["Find", "Who", "Scope", "Lines", "Review"];
    var panels = (cfg.panels || []).map(function (p) {
      return typeof p === "string" ? $(p) : p;
    });
    var acceptedEl = typeof cfg.acceptedEl === "string" ? $(cfg.acceptedEl) : cfg.acceptedEl;
    var step = 0;
    var accepted = false;
    var busy = false;

    var stagesEl = $(cfg.stagesId || "stages");
    var backBtn = $(cfg.backId || "btn-back");
    var nextBtn = $(cfg.nextId || "btn-next");
    var barLabel = $(cfg.barLabelId || "barLabel");
    var barTotal = $(cfg.barTotalId || "barTotal");
    var barSub = $(cfg.barSubId || "barSub");

    function paintTotals() {
      if (typeof cfg.getTotals !== "function") return;
      var t = cfg.getTotals() || {};
      if (barTotal && t.total != null) barTotal.textContent = t.total;
      if (barSub) {
        if (t.sub) {
          barSub.hidden = false;
          barSub.textContent = t.sub;
        } else {
          barSub.hidden = true;
        }
      }
    }

    function paintStages() {
      if (!stagesEl) return;
      stagesEl.innerHTML = labels
        .map(function (label, i) {
          var on = !accepted && i === step;
          var done = accepted || i < step;
          return (
            '<button type="button" class="gvc-stage' +
            (on ? " is-on" : done ? " is-done" : "") +
            '" data-step="' +
            i +
            '">' +
            '<span class="gvc-stage-num">' +
            (done && !on ? "✓" : String(i + 1)) +
            "</span>" +
            '<span style="white-space:nowrap">' +
            label +
            "</span></button>"
          );
        })
        .join("");
      stagesEl.querySelectorAll(".gvc-stage").forEach(function (b) {
        b.addEventListener("click", function () {
          if (accepted || busy) return;
          go(+b.getAttribute("data-step"));
        });
      });
    }

    function showPanel(i) {
      panels.forEach(function (p, idx) {
        if (!p) return;
        p.hidden = accepted || idx !== i;
      });
      if (acceptedEl) acceptedEl.hidden = !accepted;
    }

    function paintChrome() {
      paintStages();
      showPanel(step);
      paintTotals();
      if (barLabel && cfg.barLabel) barLabel.textContent = cfg.barLabel;
      if (backBtn) backBtn.hidden = accepted || step === 0;
      if (!nextBtn) return;
      nextBtn.classList.remove("gvc-btn-blocked", "is-working");
      if (accepted) {
        nextBtn.textContent = cfg.startAnotherLabel || "Start another";
      } else if (step >= labels.length - 1) {
        nextBtn.textContent = cfg.acceptLabel || "Accept";
      } else {
        nextBtn.textContent = "Continue";
      }
    }

    function go(i) {
      step = Math.max(0, Math.min(labels.length - 1, i));
      accepted = false;
      paintChrome();
      if (step === labels.length - 1 && typeof cfg.onEnterReview === "function") {
        Promise.resolve(cfg.onEnterReview()).catch(function () {});
      }
      try {
        window.scrollTo({ top: 0, behavior: "smooth" });
      } catch (_) {}
    }

    function jumpToGap(gap) {
      if (!gap) return;
      if (typeof gap.step === "number") go(gap.step);
      if (gap.focus && $(gap.focus)) {
        try {
          $(gap.focus).focus({ preventScroll: false });
          $(gap.focus).scrollIntoView({ behavior: "smooth", block: "center" });
        } catch (_) {}
      }
      if (gap.message && nextBtn) {
        nextBtn.classList.add("gvc-btn-blocked");
        nextBtn.textContent = gap.message;
      }
    }

    async function onNext() {
      if (busy) return;
      if (accepted) {
        if (typeof cfg.onStartAnother === "function") cfg.onStartAnother();
        accepted = false;
        go(0);
        return;
      }
      if (step < labels.length - 1) {
        if (typeof cfg.validateStep === "function") {
          var gap = cfg.validateStep(step);
          if (gap) {
            jumpToGap(gap);
            return;
          }
        }
        go(step + 1);
        return;
      }
      // Review → Accept
      if (typeof cfg.validateStep === "function") {
        for (var i = 0; i < labels.length - 1; i++) {
          var g = cfg.validateStep(i);
          if (g) {
            jumpToGap(g);
            return;
          }
        }
      }
      busy = true;
      if (nextBtn) {
        nextBtn.classList.add("is-working");
        nextBtn.textContent = cfg.acceptingLabel || "Accepting…";
      }
      try {
        if (typeof cfg.onAccept === "function") {
          await cfg.onAccept();
        }
        accepted = true;
        paintChrome();
      } catch (err) {
        if (nextBtn) {
          nextBtn.classList.remove("is-working");
          nextBtn.classList.add("gvc-btn-blocked");
          nextBtn.textContent = (err && err.message) || "Couldn't accept — try again";
        }
      } finally {
        busy = false;
        if (!accepted && nextBtn) {
          nextBtn.classList.remove("is-working");
          paintChrome();
        }
      }
    }

    if (backBtn) {
      backBtn.addEventListener("click", function () {
        if (accepted || step === 0) return;
        go(step - 1);
      });
    }
    if (nextBtn) nextBtn.addEventListener("click", function () { onNext(); });

    // Keep totals fresh
    document.addEventListener("input", paintTotals, true);
    document.addEventListener("change", paintTotals, true);

    paintChrome();

    return {
      go: go,
      refresh: paintChrome,
      markAccepted: function () {
        accepted = true;
        paintChrome();
      },
      step: function () {
        return step;
      },
    };
  }

  global.GvcFormStages = { mount: mount };
})(typeof window !== "undefined" ? window : globalThis);
