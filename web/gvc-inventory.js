/* GvcInventory — shared field-inventory plumbing for /ui/inventory.
 *
 * Owns the OFFLINE OUTBOX (docs/inventory/DECISIONS.md D6), the shared
 * transaction cart, recents, and the client-side photo downscale.
 *
 * Everything here is DOM-free except downscalePhoto (which checks for a
 * document at call time), so the module loads cleanly in a Node `vm`
 * sandbox for tests. No frameworks, no build step.
 *
 * Outbox contract (D6):
 *   queue = [{id, client_uuid, path, body, created_at, tries, last_error,
 *             state, label, next_at}]
 *   states: "pending"   — queued (offline or awaiting retry)
 *           "syncing"   — a POST is in flight right now
 *           "needs_attention" — server said 4xx/409; NEVER retried silently,
 *                               the user resolves it (edit-and-retry/discard)
 *   A 200 (or an idempotent "already posted") REMOVES the entry; the result
 *   is reported to the caller as state "posted".
 *   The SAME client_uuid rides every retry — the server dedups on it, so a
 *   retry can never double-post.
 */
(function (root) {
  "use strict";

  var OUTBOX_KEY_BASE = "gvc_inv_outbox_v1";
  var CART_KEY_BASE = "gvc_inv_cart_v1";
  var RECENT_KEY_BASE = "gvc_inv_recent_v1";
  var CACHE_KEY_BASE = "gvc_inv_cache_v1";
  var RECENT_MAX = 8;
  var BACKOFF_BASE_MS = 5000;       // 5s, 10s, 20s, ... capped
  var BACKOFF_CAP_MS = 5 * 60 * 1000;

  function scopedKey(base, email) {
    return base + ":" + String(email || "").trim().toLowerCase();
  }

  function uuid() {
    if (root.crypto && typeof root.crypto.randomUUID === "function") {
      return root.crypto.randomUUID();
    }
    // Fallback for ancient WebViews — good enough for dedup keys.
    var out = "";
    for (var i = 0; i < 36; i++) {
      if (i === 8 || i === 13 || i === 18 || i === 23) { out += "-"; continue; }
      if (i === 14) { out += "4"; continue; }
      var r = Math.floor(Math.random() * 16);
      if (i === 19) { r = (r & 3) | 8; }
      out += r.toString(16);
    }
    return out;
  }

  function backoffDelay(tries) {
    var d = BACKOFF_BASE_MS * Math.pow(2, Math.max(0, tries - 1));
    return Math.min(d, BACKOFF_CAP_MS);
  }

  function readJSON(storage, key, fallback) {
    try {
      var raw = storage.getItem(key);
      if (raw === null || raw === undefined || raw === "") { return fallback; }
      return JSON.parse(raw);
    } catch (e) {
      return fallback;
    }
  }

  function writeJSON(storage, key, value) {
    try { storage.setItem(key, JSON.stringify(value)); } catch (e) { /* full */ }
  }

  /* ------------------------------------------------------------ outbox */

  function createOutbox(opts) {
    opts = opts || {};
    var storage = opts.storage || root.localStorage;
    var key = scopedKey(opts.keyBase || OUTBOX_KEY_BASE, opts.email);
    var fetchFn = opts.fetch ||
      (typeof root.fetch === "function" ? root.fetch.bind(root) : null);
    var now = opts.now || function () { return Date.now(); };
    var onChange = opts.onChange || function () {};
    var syncing = false;

    function load() { return readJSON(storage, key, []); }
    function save(q) { writeJSON(storage, key, q); onChange(q); }

    function enqueue(path, body, meta) {
      meta = meta || {};
      var q = load();
      var entry = {
        id: uuid(),
        client_uuid: (body && body.client_uuid) || "",
        path: path,
        body: body,
        created_at: new Date(now()).toISOString(),
        tries: 0,
        last_error: "",
        state: "pending",
        label: meta.label || "",
        next_at: 0
      };
      q.push(entry);
      save(q);
      return entry;
    }

    function list() { return load(); }

    function counts() {
      var q = load();
      var pending = 0, attention = 0;
      for (var i = 0; i < q.length; i++) {
        if (q[i].state === "needs_attention") { attention++; }
        else { pending++; }
      }
      return { total: q.length, pending: pending, attention: attention };
    }

    function find(q, id) {
      for (var i = 0; i < q.length; i++) {
        if (q[i].id === id) { return i; }
      }
      return -1;
    }

    function discard(id) {
      var q = load();
      var i = find(q, id);
      if (i === -1) { return null; }
      var gone = q.splice(i, 1)[0];
      save(q);
      return gone;
    }

    function update(id, newBody) {
      // Edit-and-retry: replace the payload but KEEP the original
      // client_uuid — the server's dedup identity for this attempt.
      var q = load();
      var i = find(q, id);
      if (i === -1) { return null; }
      var entry = q[i];
      if (entry.client_uuid && newBody && typeof newBody === "object") {
        newBody.client_uuid = entry.client_uuid;
      }
      entry.body = newBody;
      entry.state = "pending";
      entry.next_at = 0;
      entry.last_error = "";
      save(q);
      return entry;
    }

    function retry(id) {
      // Explicit user retry re-arms a needs_attention item as pending.
      var q = load();
      var i = find(q, id);
      if (i === -1) { return null; }
      q[i].state = "pending";
      q[i].next_at = 0;
      save(q);
      return q[i];
    }

    function envelopeOf(data) {
      if (data && typeof data === "object") {
        if (data.detail && typeof data.detail === "object") { return data.detail; }
        if (data.code || data.detail) { return data; }
      }
      return null;
    }

    function sync(syncOpts) {
      // Walks pending entries oldest-first. Returns a Promise of
      // [{entry, state, data?}] results. needs_attention entries are
      // NEVER touched here — only retry()/update() re-arm them.
      syncOpts = syncOpts || {};
      var force = !!syncOpts.force;
      if (syncing) { return Promise.resolve([]); }
      if (!fetchFn) { return Promise.resolve([]); }
      syncing = true;
      var results = [];

      function step() {
        var q = load();
        var entry = null;
        for (var i = 0; i < q.length; i++) {
          var e = q[i];
          if (e.state !== "pending") { continue; }
          if (!force && e.next_at && now() < e.next_at) { continue; }
          entry = e;
          break;
        }
        if (!entry) {
          syncing = false;
          onChange(load());
          return Promise.resolve(results);
        }
        entry.state = "syncing";
        save(q);
        return fetchFn(entry.path, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify(entry.body)
        }).then(function (resp) {
          var parse = typeof resp.json === "function"
            ? resp.json().catch(function () { return null; })
            : Promise.resolve(null);
          return parse.then(function (data) {
            var q2 = load();
            var idx = find(q2, entry.id);
            if (idx === -1) {           // discarded mid-flight
              return;
            }
            if (resp.ok) {
              q2.splice(idx, 1);        // a 200 removes it — it is POSTED
              save(q2);
              results.push({ entry: entry, state: "posted", data: data });
              return;
            }
            var env = envelopeOf(data) || {
              code: "HTTP_" + resp.status,
              detail: "The server refused this (" + resp.status + ")."
            };
            if (resp.status >= 400 && resp.status < 500) {
              // Server understood and said no (409 stock, 422 validation…).
              // Retrying the same payload can never succeed — park it for
              // the user. NOT retried silently.
              q2[idx].state = "needs_attention";
              q2[idx].last_error = (env.detail || env.code || "") +
                (env.advice ? " — " + env.advice : "");
              save(q2);
              results.push({ entry: q2[idx], state: "needs_attention", data: env });
            } else {
              // 5xx — server trouble; back off and retry later with the
              // SAME client_uuid.
              q2[idx].state = "pending";
              q2[idx].tries += 1;
              q2[idx].next_at = now() + backoffDelay(q2[idx].tries);
              q2[idx].last_error = env.detail || ("Server error " + resp.status);
              save(q2);
              results.push({ entry: q2[idx], state: "pending", data: env });
            }
          });
        }).catch(function (err) {
          // Network failure — still offline. Same client_uuid next time.
          var q3 = load();
          var idx3 = find(q3, entry.id);
          if (idx3 !== -1) {
            q3[idx3].state = "pending";
            q3[idx3].tries += 1;
            q3[idx3].next_at = now() + backoffDelay(q3[idx3].tries);
            q3[idx3].last_error = String((err && err.message) || err || "offline");
            save(q3);
            results.push({ entry: q3[idx3], state: "pending" });
          }
        }).then(function () {
          var last = results.length ? results[results.length - 1] : null;
          if (last && last.state === "pending") {
            // That one is waiting again (offline / 5xx) — stop the walk;
            // hammering the rest right now would fail the same way.
            syncing = false;
            onChange(load());
            return results;
          }
          return step();
        });
      }

      return Promise.resolve().then(step).then(function (r) {
        syncing = false;
        return r || results;
      }, function (err) {
        syncing = false;
        throw err;
      });
    }

    return {
      key: key,
      enqueue: enqueue,
      list: list,
      counts: counts,
      discard: discard,
      update: update,
      retry: retry,
      sync: sync
    };
  }

  /* -------------------------------------------------------------- cart */

  function newCart(type) {
    // client_uuid is generated ONCE here and kept through every retry —
    // it is the transaction's identity for server-side dedup.
    return {
      client_uuid: uuid(),
      type: type || "",
      src: "",
      dst: "",
      src_name: "",
      dst_name: "",
      lines: [],
      created_at: new Date().toISOString()
    };
  }

  function cartAddQty(cart, item, qty, unit) {
    var q = Number(qty);
    if (!(q > 0)) { return null; }
    for (var i = 0; i < cart.lines.length; i++) {
      var ln = cart.lines[i];
      if (ln.kind === "quantity" && ln.item_id === item.id && ln.unit === unit) {
        ln.qty = String(Number(ln.qty) + q);
        return ln;
      }
    }
    var line = {
      kind: "quantity", item_id: item.id, name: item.name,
      qty: String(q), unit: unit, base_unit: item.base_unit || ""
    };
    cart.lines.push(line);
    return line;
  }

  function cartAddAsset(cart, asset) {
    for (var i = 0; i < cart.lines.length; i++) {
      if (cart.lines[i].kind === "asset" &&
          cart.lines[i].asset_id === asset.id) {
        return null;                  // duplicate-scan prevention
      }
    }
    var line = { kind: "asset", asset_id: asset.id, name: asset.name || asset.id };
    cart.lines.push(line);
    return line;
  }

  function cartAddKit(cart, kit) {
    for (var i = 0; i < cart.lines.length; i++) {
      if (cart.lines[i].kind === "kit" && cart.lines[i].kit_id === kit.id) {
        return null;
      }
    }
    var line = { kind: "kit", kit_id: kit.id, name: kit.name || kit.id };
    cart.lines.push(line);
    return line;
  }

  function cartRemoveLine(cart, index) {
    if (index < 0 || index >= cart.lines.length) { return null; }
    return cart.lines.splice(index, 1)[0];
  }

  function cartToTxn(cart) {
    var lines = [];
    for (var i = 0; i < cart.lines.length; i++) {
      var ln = cart.lines[i];
      if (ln.kind === "quantity") {
        lines.push({ item_id: ln.item_id, qty: ln.qty, unit: ln.unit });
      } else if (ln.kind === "asset") {
        lines.push({ asset_id: ln.asset_id });
      } else if (ln.kind === "kit") {
        lines.push({ kit_id: ln.kit_id });
      }
    }
    var txn = { client_uuid: cart.client_uuid, type: cart.type, lines: lines };
    if (cart.src) { txn.src = cart.src; }
    if (cart.dst) { txn.dst = cart.dst; }
    return txn;
  }

  function saveCart(storage, email, cart) {
    writeJSON(storage, scopedKey(CART_KEY_BASE, email), cart);
  }

  function loadCart(storage, email) {
    var cart = readJSON(storage, scopedKey(CART_KEY_BASE, email), null);
    if (!cart || !cart.client_uuid || !Array.isArray(cart.lines)) { return null; }
    return cart;
  }

  function clearCart(storage, email) {
    try { storage.removeItem(scopedKey(CART_KEY_BASE, email)); } catch (e) { /* */ }
  }

  /* ------------------------------------------------------------ recents */

  function loadRecents(storage, email) {
    var r = readJSON(storage, scopedKey(RECENT_KEY_BASE, email), null);
    if (!r || typeof r !== "object") { r = {}; }
    if (!Array.isArray(r.items)) { r.items = []; }
    if (!Array.isArray(r.locations)) { r.locations = []; }
    return r;
  }

  function pushRecent(storage, email, kind, entry) {
    if (!entry || !entry.id) { return; }
    var r = loadRecents(storage, email);
    var list = kind === "location" ? r.locations : r.items;
    var next = [{ id: entry.id, name: entry.name || entry.id }];
    for (var i = 0; i < list.length && next.length < RECENT_MAX; i++) {
      if (list[i].id !== entry.id) { next.push(list[i]); }
    }
    if (kind === "location") { r.locations = next; } else { r.items = next; }
    writeJSON(storage, scopedKey(RECENT_KEY_BASE, email), r);
  }

  /* ---------------------------------------------- cached lists ("as of") */

  function cachePut(storage, email, name, data) {
    var c = readJSON(storage, scopedKey(CACHE_KEY_BASE, email), {});
    c[name] = { data: data, at: new Date().toISOString() };
    writeJSON(storage, scopedKey(CACHE_KEY_BASE, email), c);
  }

  function cacheGet(storage, email, name) {
    var c = readJSON(storage, scopedKey(CACHE_KEY_BASE, email), {});
    return c[name] || null;          // {data, at} or null — caller shows "as of"
  }

  /* ------------------------------------------------------ photo shrink */

  function downscalePhoto(file, opts, cb) {
    // Client-side downscale to a JPEG dataURL. max ~1000px long edge,
    // quality walks down from 0.7 until the dataURL fits under maxChars
    // (the unknown-item endpoint caps the field server-side).
    opts = opts || {};
    var maxDim = opts.maxDim || 1000;
    var quality = opts.quality || 0.7;
    var maxChars = opts.maxChars || 380000;
    var doc = root.document;
    if (!doc || !root.URL || typeof root.Image !== "function") {
      cb(new Error("This device can't resize photos here."));
      return;
    }
    var url = root.URL.createObjectURL(file);
    var img = new root.Image();
    img.onload = function () {
      try {
        var w = img.naturalWidth || img.width;
        var h = img.naturalHeight || img.height;
        var scale = Math.min(1, maxDim / Math.max(w, h, 1));
        var canvas = doc.createElement("canvas");
        canvas.width = Math.max(1, Math.round(w * scale));
        canvas.height = Math.max(1, Math.round(h * scale));
        canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
        var q = quality;
        var data = canvas.toDataURL("image/jpeg", q);
        while (data.length > maxChars && q > 0.25) {
          q -= 0.1;
          data = canvas.toDataURL("image/jpeg", q);
        }
        root.URL.revokeObjectURL(url);
        if (data.length > maxChars) {
          cb(new Error("Photo is too detailed to attach — try a wider shot."));
          return;
        }
        cb(null, data);
      } catch (e) {
        root.URL.revokeObjectURL(url);
        cb(e);
      }
    };
    img.onerror = function () {
      root.URL.revokeObjectURL(url);
      cb(new Error("Could not read that photo."));
    };
    img.src = url;
  }

  root.GvcInventory = {
    OUTBOX_KEY_BASE: OUTBOX_KEY_BASE,
    CART_KEY_BASE: CART_KEY_BASE,
    RECENT_KEY_BASE: RECENT_KEY_BASE,
    CACHE_KEY_BASE: CACHE_KEY_BASE,
    scopedKey: scopedKey,
    uuid: uuid,
    backoffDelay: backoffDelay,
    createOutbox: createOutbox,
    newCart: newCart,
    cartAddQty: cartAddQty,
    cartAddAsset: cartAddAsset,
    cartAddKit: cartAddKit,
    cartRemoveLine: cartRemoveLine,
    cartToTxn: cartToTxn,
    saveCart: saveCart,
    loadCart: loadCart,
    clearCart: clearCart,
    loadRecents: loadRecents,
    pushRecent: pushRecent,
    cachePut: cachePut,
    cacheGet: cacheGet,
    downscalePhoto: downscalePhoto
  };
})(typeof window !== "undefined" ? window
   : (typeof globalThis !== "undefined" ? globalThis : this));
