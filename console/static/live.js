// Live view: turns the platform's event stream into the picture.
// Every dot, step, check and seam on screen comes from an event the
// platform sent (live) or recorded (replay). Nothing here invents data.
(function () {
  "use strict";
  var root = document.querySelector(".live");
  if (!root) return;

  var canLive = root.dataset.live === "1";
  var recordings = JSON.parse(root.dataset.recordings || "{}");
  var recMeta = JSON.parse(root.dataset.meta || "{}");
  var repairs = parseInt(root.dataset.repairs || "0", 10);
  var csrf = root.dataset.csrf;

  var $ = function (id) { return document.getElementById(id); };
  var go = $("live-go"), replayBtn = $("live-replay"), pick = $("live-bug");
  var stateEl = $("live-state"), clockEl = $("live-clock"), modeEl = $("live-mode");
  var patchEl = $("live-patch"), attemptEl = $("p-attempt");
  var canvas = $("live-canvas"), rateCanvas = $("live-rate"), rateNum = $("live-rate-num");
  var bowl = $("live-bowl"), stamp = $("live-stamp"), outcomeEl = $("live-outcome");
  var source = null, ended = true, lastT = 0, lastWall = 0, replaying = false;
  var samples = [], lanes = {};
  var logEl = $("eng-log"), srcEl = $("eng-src"), fileEl = $("eng-file");
  var rateEl = $("eng-rate"), countEl = $("eng-count");
  var lastDiff = null;
  var srcWin = null, logLines = 0, tokChars = 0, tokStart = 0, trafficLogged = 0;
  var undoBtn = $("live-undo"), redoBtn = $("live-redo"), keptRun = null, keepTimer = null;
  function showUndo(mode) {          // mode: "undo", "redo" or null
    if (!undoBtn) return;
    undoBtn.hidden = mode !== "undo"; redoBtn.hidden = mode !== "redo";
    undoBtn.disabled = redoBtn.disabled = false;
  }

  // ----------------------------------------------------- inside the repair
  function esc(t) { return String(t == null ? "" : t).replace(/[&<>]/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]; }); }
  var TOK = /(#.*$)|("[^"]*"|'[^']*')|\b(def|return|if|elif|else|for|in|not|and|or|raise|try|except|import|from|None|True|False|with|as|is|lambda|class)\b/g;
  function hl(code) {               // light Python colouring in one pass over escaped text
    return esc(code).replace(TOK, function (m, com, str, kw) {
      return '<span class="' + (com ? "com" : str ? "str" : "kw") + '">' + m + "</span>";
    });
  }
  function shortPath(p) {           // never show a machine's folders, only the file
    var parts = String(p || "").split("/");
    return parts.slice(-2).join("/");
  }
  function log(t, tag, html) {
    if (!logEl) return;
    if (!logLines) logEl.innerHTML = "";
    var line = document.createElement("span");
    line.className = "l";
    line.innerHTML = '<span class="ts">' + (t == null ? "         " : "[" + t.toFixed(2).padStart(6) + "s]") + "</span> " +
      '<span class="tag">' + esc(tag) + "</span> " + html;
    logEl.appendChild(line);
    logLines++;
    if (countEl) countEl.textContent = logLines + " lines";
    logEl.scrollTop = logEl.scrollHeight;
  }
  function renderSource(win, patched) {
    if (!srcEl || !win) return;
    fileEl.textContent = win.file + ":" + win.line;
    var rows = [], removed = {}, added = {};
    if (patched) {                  // overlay the admitted diff on the window it touches
      var oldNo = 0, newNo = 0;
      patched.split("\n").forEach(function (l) {
        var m = l.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)/);
        if (m) { oldNo = +m[1]; newNo = +m[2]; return; }
        if (!oldNo || l.startsWith("---") || l.startsWith("+++")) return;
        if (l.startsWith("-")) { removed[oldNo] = true; oldNo++; }
        else if (l.startsWith("+")) { (added[oldNo] = added[oldNo] || []).push(l.slice(1)); newNo++; }
        else { oldNo++; newNo++; }
      });
    }
    win.lines.forEach(function (code, i) {
      var no = win.start + i;
      (added[no] || []).forEach(function (n) {
        rows.push('<span class="row new"><span class="ln">+</span>' + hl(n) + "</span>");
      });
      var cls = removed[no] ? "gone" : (no === win.line && !patched ? "bad" : "");
      rows.push('<span class="row ' + cls + '"><span class="ln">' + no + "</span>" + hl(code) + "</span>");
    });
    srcEl.innerHTML = rows.join("");
    var mark = srcEl.querySelector(".row.bad, .row.new");
    if (mark) srcEl.scrollTop = Math.max(0, mark.offsetTop - 120);
  }

  function css(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }
  function plain(reason) {
    var r = (reason || "").toLowerCase();
    if (r.indexOf("did not recur") >= 0) return "The error would not happen again on purpose, so it cannot be tested. Left for a person.";
    if (r.indexOf("outbound call") >= 0) return "The error happens by chance, so it cannot be repeated. Left for a person.";
    if (r.indexOf("no candidate passed") >= 0) return "None of the AI's fixes passed the checks. Handed to a person.";
    return reason || "";
  }

  // ------------------------------------------------------------ the bowl
  function rng(seed) {
    return function () {
      seed |= 0; seed = seed + 0x6D2B79F5 | 0;
      var t = Math.imul(seed ^ seed >>> 15, 1 | seed);
      t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
      return ((t ^ t >>> 14) >>> 0) / 4294967296;
    };
  }
  function halfWidth(y) {           // the bowl body narrows towards its foot
    var k = Math.min(1, Math.max(0, (y - 92) / 158));
    return 150 - 92 * Math.pow(k, 1.6);
  }
  function seamPath(i) {
    var r = rng(i * 7919 + 13), y = 100 + r() * 6;
    var x = 210 + (r() * 2 - 1) * (halfWidth(y) - 18);
    var d = "M" + x.toFixed(1) + " " + y.toFixed(1);
    var steps = 4 + Math.floor(r() * 4);
    for (var s = 0; s < steps; s++) {
      y += 16 + r() * 20;
      if (y > 244) break;
      x += (r() * 2 - 1) * 18;
      var hw = halfWidth(y) - 10;
      x = Math.max(210 - hw, Math.min(210 + hw, x));
      d += " L" + x.toFixed(1) + " " + y.toFixed(1);
    }
    return d;
  }
  function drawBowl() {
    var ns = "http://www.w3.org/2000/svg", seams = [];
    for (var i = 0; i < repairs; i++) seams.push('<path class="seam-old" d="' + seamPath(i) + '"/>');
    bowl.innerHTML =
      '<ellipse cx="210" cy="268" rx="132" ry="12" style="fill:var(--surface-2)"/>' +
      '<path d="M58 92 C58 92 60 214 150 250 C180 262 240 262 270 250 C360 214 362 92 362 92 Z" ' +
      'style="fill:var(--surface-2);stroke:var(--ink-3);stroke-width:1.4"/>' +
      '<path d="M168 252 L172 266 L248 266 L252 252" style="fill:none;stroke:var(--ink-3);stroke-width:1.4"/>' +
      seams.join("") +
      '<ellipse cx="210" cy="92" rx="152" ry="26" style="fill:var(--surface);stroke:var(--ink-3);stroke-width:1.4"/>' +
      '<ellipse cx="210" cy="94" rx="136" ry="18" style="fill:var(--bg);opacity:.8"/>';
    void ns;
  }
  function addSeam() {
    // a replayed repair is already in the count: draw its seam, add nothing
    var p = document.createElementNS("http://www.w3.org/2000/svg", "path");
    p.setAttribute("class", "seam-new");
    p.setAttribute("pathLength", "1");
    p.setAttribute("d", seamPath(repairs));
    var rim = bowl.querySelectorAll("ellipse")[1];
    bowl.insertBefore(p, rim);
    if (replaying) return;
    repairs += 1;
    $("live-repairs").textContent = repairs.toLocaleString();
  }
  drawBowl();

  async function sealSvg(hash) {
    var bits = "";
    try {
      var buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(hash));
      new Uint8Array(buf).forEach(function (b) { bits += b.toString(2).padStart(8, "0"); });
    } catch (e) { bits = hash.split("").map(function (c) { return (c.charCodeAt(0) & 1).toString(); }).join("").padEnd(15, "0"); }
    var cells = "", k = 0;
    for (var row = 0; row < 5; row++) for (var col = 0; col < 3; col++, k++) {
      if (bits[k] !== "1") continue;
      [col, 4 - col].filter(function (v, i, a) { return a.indexOf(v) === i; }).forEach(function (c) {
        cells += '<rect x="' + (6 + c * 5.6).toFixed(1) + '" y="' + (6 + row * 5.6).toFixed(1) + '" width="5.6" height="5.6"/>';
      });
    }
    return '<svg width="78" height="78" viewBox="0 0 40 40" aria-hidden="true"><rect x="1.5" y="1.5" width="37" height="37" rx="3" fill="none" stroke="var(--broken)" stroke-width="2.4"/><g fill="var(--broken)">' + cells + "</g></svg>";
  }

  // ------------------------------------------------------------ traffic
  function sizeCanvas(c) {         // resize only when the layout changed
    var r = c.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    var w = Math.round(r.width * dpr), h = Math.round(r.height * dpr);
    if (c.width !== w || c.height !== h) {
      c.width = w; c.height = h;
      c.getContext("2d").setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    return r;
  }
  var WINDOW = 12;
  function now() { return ended ? lastT : lastT + (performance.now() - lastWall) / 1000; }
  function draw() {
    var r = sizeCanvas(canvas), ctx = canvas.getContext("2d"), t = now();
    ctx.clearRect(0, 0, r.width, r.height);
    var ok = css("--fixed"), bad = css("--broken"), grid = css("--rule");
    var names = Object.keys(lanes), n = Math.max(names.length, 1);
    ctx.strokeStyle = grid; ctx.lineWidth = 1;
    for (var s = 0; s <= WINDOW; s += 2) {
      var gx = r.width - (s / WINDOW) * r.width;
      ctx.beginPath(); ctx.moveTo(gx, 0); ctx.lineTo(gx, r.height); ctx.stroke();
    }
    for (var i = samples.length - 1; i >= 0; i--) {
      var sm = samples[i], age = t - sm[0];
      if (age > WINDOW) break;
      if (age < 0) continue;
      var x = r.width - (age / WINDOW) * r.width;
      var lane = lanes[sm[2]], y = 14 + ((lane + 0.5) / n) * (r.height - 28) + sm[3] * 10;
      ctx.fillStyle = sm[1] >= 500 ? bad : ok;
      ctx.globalAlpha = Math.max(0.25, 1 - age / WINDOW);
      ctx.beginPath(); ctx.arc(x, y, sm[1] >= 500 ? 3.4 : 2.4, 0, Math.PI * 2); ctx.fill();
    }
    ctx.globalAlpha = 1;
    // failure rate over the last second, and its history
    var rr = sizeCanvas(rateCanvas), rc = rateCanvas.getContext("2d");
    rc.clearRect(0, 0, rr.width, rr.height);
    var bins = 60, pts = [];
    for (var b = 0; b < bins; b++) {
      var hi = t - (b / bins) * WINDOW, lo = hi - 1, tot = 0, err = 0;
      for (var j = samples.length - 1; j >= 0 && samples[j][0] >= lo - 0.01; j--) {
        if (samples[j][0] <= hi) { tot++; if (samples[j][1] >= 500) err++; }
      }
      pts.push(tot ? err / tot : 0);
    }
    rc.strokeStyle = bad; rc.lineWidth = 2; rc.beginPath();
    pts.forEach(function (v, b) {
      var x = rr.width - (b / (bins - 1)) * rr.width, y = rr.height - 3 - v * (rr.height - 6);
      b ? rc.lineTo(x, y) : rc.moveTo(x, y);
    });
    rc.stroke();
    rateNum.textContent = Math.round(pts[0] * 100) + "%";
    if (!ended) clockEl.textContent = (t).toFixed(1) + " s";
    requestAnimationFrame(draw);
  }
  requestAnimationFrame(draw);

  // -------------------------------------------------------------- steps
  function step(name) { return document.querySelector('.lsteps li[data-step="' + name + '"]'); }
  function setStep(name, state, text, t) {
    var li = step(name);
    if (!li) return;
    li.className = state || "";
    if (text !== undefined) {
      var span = li.querySelector("span");
      if (span) span.textContent = text;
    }
    if (t !== undefined) {
      var w = li.querySelector(".when") || document.createElement("em");
      w.className = "when"; w.textContent = t.toFixed(1) + " s";
      li.querySelector("b").appendChild(w);
    }
  }
  function setState(cls, text) { stateEl.className = "state " + cls; stateEl.textContent = text; }

  var DEFAULT_TEXT = {};
  document.querySelectorAll(".lsteps li").forEach(function (li) {
    DEFAULT_TEXT[li.dataset.step] = li.querySelector("span").textContent;
  });
  var COND = { structural: "not just hiding the error", applies: "written correctly",
               reproduction: "the error is gone", suite: "nothing else broke" };

  function reset() {
    samples = []; lanes = {}; lastT = 0; lastWall = performance.now(); ended = false;
    document.querySelectorAll(".lsteps li").forEach(function (li) {
      li.className = ""; var w = li.querySelector(".when"); if (w) w.remove();
      li.querySelector("span").textContent = DEFAULT_TEXT[li.dataset.step];
    });
    document.querySelectorAll("#st-dots i").forEach(function (d) { d.className = ""; });
    $("st-chips").innerHTML = "";
    patchEl.innerHTML = '<span class="muted">The patch appears here as the model writes it.</span>';
    attemptEl.textContent = ""; stamp.hidden = true; stamp.innerHTML = "";
    var ex = document.getElementById("live-explain"); if (ex) ex.hidden = true;
    outcomeEl.textContent = "Every repair adds a seam.";
    setState("", "Starting");
    srcWin = null; logLines = 0; tokChars = 0; trafficLogged = 0;
    if (logEl) logEl.innerHTML = '<span class="muted">Waiting for the platform…</span>';
    if (srcEl) srcEl.innerHTML = '<span class="muted">The crashing file appears here, with the failing line marked.</span>';
    if (fileEl) fileEl.textContent = "waiting for a crash";
    if (rateEl) rateEl.textContent = ""; if (countEl) countEl.textContent = "";
  }

  function renderPatch(text, final) {
    patchEl.textContent = "";
    text.split("\n").forEach(function (line, i, arr) {
      var span = document.createElement("span");
      if (final) {
        if (line.startsWith("@@")) span.className = "d-hunk";
        else if (line.startsWith("+") && !line.startsWith("+++")) span.className = "d-add";
        else if (line.startsWith("-") && !line.startsWith("---")) span.className = "d-del";
      }
      span.textContent = line + (i < arr.length - 1 ? "\n" : "");
      patchEl.appendChild(span);
    });
    if (!final) { var c = document.createElement("i"); c.className = "caret"; patchEl.appendChild(c); }
    patchEl.scrollTop = patchEl.scrollHeight;
  }

  var COND_LONG = { structural: "structure", applies: "applies", reproduction: "reproduction",
                    suite: "test suite" };
  function engine(e) {
    var t = e.t, ok = '<span class="ok">✓</span>', no = '<span class="no">✗</span>';
    switch (e.kind) {
      case "replay":
        log(null, "replay", '<span class="dim">playing back a recorded run at its real pace</span>'); break;
      case "start":
        log(t, "run", "bug <span class=hi>" + esc(e.entry) + "</span> · " + esc(e.title) +
            ' <span class="dim">(' + esc(e.service || "") + ")</span>"); break;
      case "traffic_started":
        log(t, "load", "traffic generator started against both services"); break;
      case "traffic":
        trafficLogged += e.samples.length;
        if (trafficLogged >= 200 && trafficLogged - e.samples.length < 200)
          log(t, "load", trafficLogged + " requests so far, every one checked"); break;
      case "fault_deployed":
        log(t, "deploy", "faulty release rolled onto <span class=hi>" + esc(e.service) + "</span>"); break;
      case "captured":
        log(t, "capture", no + " <span class=hi>" + esc(e.exception_type) + "</span> " + esc(e.message || "") +
            ' <span class="dim">on ' + esc((e.method || "") + " " + (e.path || "")) + "</span>");
        (e.frames || []).forEach(function (f) {
          log(null, "", '<span class="dim">  at ' + esc(f.function) + " (" + esc(f.file) + ":" + f.line + ")</span>" +
              (f.code ? "  " + esc(f.code) : ""));
        });
        if (e.headers !== undefined)
          log(null, "", '<span class="dim">  request kept: ' + e.headers + " headers (secrets redacted), " +
              e.outbound + " outbound call" + (e.outbound === 1 ? "" : "s") + " recorded for hermetic replay</span>");
        if (e.source) { srcWin = e.source; renderSource(srcWin, null); }
        break;
      case "reproduce_start":
        log(t, "sandbox", "fresh process per replay; outbound calls served from the recording"); break;
      case "reproduce_done":
        (e.verdicts || (e.replays || []).map(function (h) { return { failed: h }; })).forEach(function (v, i, arr) {
          log(null, "replay", (i + 1) + "/" + arr.length + " " + (v.failed ? '<span class="no">FAIL</span>' : '<span class="ok">pass</span>') +
              (v.exception_type ? " " + esc(v.exception_type) + "@" + esc(v.top_frame || "") : "") +
              (v.status_code ? ' <span class="dim">HTTP ' + v.status_code + "</span>" : ""));
        });
        log(t, "verdict", e.reproduced ?
            ok + " deterministic: signature <span class=hi>" + esc(e.signature || "") + "</span>" :
            no + " refused: " + esc(e.reason)); break;
      case "context":
        (e.files || []).forEach(function (f) {
          log(null, "evidence", esc(shortPath(f.file)) + ":" + f.line + " in " + esc(f.function) +
              ' <span class="dim">(' + f.lines + " lines of source)</span>");
        });
        log(t, "evidence", e.frames + " app frames, " + (e.release_diff ? "release diff, " : "") +
            e.prior_failures + " earlier rejection" + (e.prior_failures === 1 ? "" : "s") +
            (e.memory_notes ? ", " + e.memory_notes + " memory notes" : "")); break;
      case "candidate_start":
        tokChars = 0; tokStart = 0;
        log(t, "model", "streaming attempt " + e.attempt + " (patch only; the model cannot run code)"); break;
      case "token":
        if (!tokStart) tokStart = performance.now();
        tokChars += e.text.length;
        var secs = (performance.now() - tokStart) / 1000;
        if (rateEl && secs > 0.3) rateEl.textContent = Math.round(tokChars / 4 / secs) + " tokens/s";
        break;
      case "gate_result":
        var lines = (e.diff || "").split("\n").filter(function (l) { return /^[+-]/.test(l) && !/^(\+\+\+|---)/.test(l); }).length;
        log(t, "patch", "attempt " + e.attempt + ": " + lines + " changed line" + (lines === 1 ? "" : "s") +
            ' <span class="dim">(' + tokChars + " characters)</span>");
        ["structural", "applies", "reproduction", "suite"].forEach(function (k) {
          if (!(k in (e.conditions || {}))) return;
          var d = (e.details || {})[k];
          log(null, "gate", (e.conditions[k] ? ok : no) + " " + COND_LONG[k] + (d ? ' <span class="dim">' + esc(d) + "</span>" : ""));
        });
        if (e.admitted) {
          log(t, "gate", '<span class="ok">ADMITTED</span>: verification record signed (HMAC-SHA256)');
          lastDiff = e.diff;
          if (srcWin) renderSource(srcWin, e.diff);
        } else {
          log(t, "gate", '<span class="no">REJECTED</span>: the failure report goes back to the model');
        }
        break;
      case "escalated": log(t, "gate", no + " " + esc(e.reason)); break;
      case "refused": log(t, "refuse", no + " " + esc(e.reason)); break;
      case "deploying": log(t, "deploy", "only a gate-signed patch may deploy; record verified"); break;
      case "deployed": log(t, "deploy", ok + " " + esc(e.service) + " restarted on the patched tree"); break;
      case "probe": log(t, "probe", (e.passed ? ok : no) + " the original failing request, replayed live"); break;
      case "recovered": log(t, "traffic", ok + " error rate back to zero; mended in <span class=hi>" + e.mttr.toFixed(2) + " s</span>");
        if (srcWin && lastDiff) renderSource(srcWin, lastDiff); break;
      case "certificate":
        log(t, "cert", "#" + String(e.sequence).padStart(6, "0") + " sha256 <span class=hi>" + esc((e.hash || "").slice(0, 24)) + "…</span>");
        if (e.prev_hash) log(null, "cert", '<span class="dim">chained to ' + esc(e.prev_hash.slice(0, 24)) + "…</span>");
        if (e.ed25519) log(null, "cert", '<span class="dim">ed25519 ' + esc(e.ed25519) + "…</span>");
        break;
      case "pull_request": if (e.url) log(t, "github", ok + " pull request #" + e.number + " opened"); break;
      case "undo_start": log(t, "undo", "putting the faulty release back on the running shop"); break;
      case "undo_done": log(t, "undo", no + " the bug is back: " + e.errors + " failing requests seen");
        if (srcWin) renderSource(srcWin, null); break;
      case "redo_start": log(t, "redo", "re-applying certificate #" + String(e.sequence).padStart(6, "0") + " (no AI call)"); break;
      case "cert_verified":
        ["hash", "chain", "record", "patch", "ed25519"].forEach(function (k) {
          if (e[k] === null || e[k] === undefined) return;
          var label = { hash: "certificate hash recomputed", chain: "chain intact back to genesis",
                        record: "gate's signed record verified", patch: "patch is the exact diff the gate signed",
                        ed25519: "Ed25519 signature verified" }[k];
          log(null, "verify", (e[k] ? ok : no) + " " + label);
        });
        break;
      case "redo_refused": log(t, "redo", no + " " + esc(e.reason)); break;
      case "false_fix": log(t, "probe", no + " the error is still there"); break;
      case "error": log(t, "error", no + " " + esc(e.message)); break;
      case "end": log(t, "end", "outcome: <span class=hi>" + esc(e.outcome || "") + "</span>"); break;
    }
  }

  var patchText = "", certLink = null, currentRun = null, redoT = 0;
  function armKeep() {               // the shop is kept for three minutes after each action
    clearTimeout(keepTimer);
    keepTimer = setTimeout(function () { keptRun = null; showUndo(null); }, 175000);
  }
  function act(kind) {
    if (!keptRun) return;
    undoBtn.disabled = redoBtn.disabled = true; go.disabled = true;
    fetch("/live/" + keptRun + "/" + kind, { method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: "csrf=" + encodeURIComponent(csrf) })
      .then(function (r) { return r.json().then(function (j) { return [r.ok, j]; }); })
      .then(function (res) {
        if (!res[0]) { modeEl.textContent = res[1].error || "That did not work."; showUndo(kind); go.disabled = false; return; }
        armKeep(); listenMore("/live/stream/" + res[1].run);
      });
  }
  if (undoBtn) { undoBtn.addEventListener("click", function () { act("undo"); });
                 redoBtn.addEventListener("click", function () { act("redo"); }); }
  function handle(e) {
    if (e.t !== undefined && e.kind !== "replay") { lastT = e.t; lastWall = performance.now(); }
    try { engine(e); } catch (err) { /* the picture must never stop the run */ }
    switch (e.kind) {
      case "replay":
        replaying = true;
        modeEl.classList.add("replaying");
        modeEl.textContent = "Replay of a real live run recorded " + (e.recorded || "") +
          ", with model " + (e.model || "") + ". Played back at the pace it happened.";
        break;
      case "start":
        setState("healthy", "Healthy");
        outcomeEl.textContent = "Bug " + e.entry + ": " + e.title;
        for (var oi = 0; oi < pick.options.length; oi++) {   // follow a replay opened by link
          if (pick.options[oi].value === e.entry) { pick.value = e.entry; break; }
        }
        break;
      case "traffic":
        e.samples.forEach(function (s) {
          if (!(s[2] in lanes)) lanes[s[2]] = Object.keys(lanes).length;
          samples.push([s[0], s[1], s[2], Math.random() * 2 - 1]);
        });
        if (samples.length > 6000) samples = samples.slice(-4000);
        break;
      case "fault_deployed":
        setStep("fault", "done", undefined, e.t);
        setState("broken", "Broken");
        setStep("captured", "active");
        break;
      case "captured":
        setStep("captured", "done", (e.method || "") + " " + (e.path || "") + " crashed with " +
          (e.exception_type || "an error") + (e.again ? " (a second failure)" : "") + ".", e.t);
        break;
      case "reproduce_start":
        setStep("replay", "active"); setState("mending", "Mending"); break;
      case "reproduce_done":
        (e.replays || []).forEach(function (hit, i) {
          setTimeout(function () {
            var d = document.querySelectorAll("#st-dots i")[i];
            if (d) d.className = hit ? "hit" : "miss";
          }, i * 140);
        });
        if (e.reproduced) {
          var n = (e.replays || []).length;
          setStep("replay", "done", "Failed the same way " + n + " out of " + n + " times. It is real and repeatable.", e.t);
        } else {
          setStep("replay", "alone", plain(e.reason), e.t);
        }
        break;
      case "refused":
        ["ai", "gate", "deploy", "recovered", "certificate"].forEach(function (s) { setStep(s, "skipped"); });
        setState("alone", "Left alone");
        break;
      case "candidate_start":
        setStep("ai", "active", "The model is writing attempt " + e.attempt + ".");
        attemptEl.textContent = "Attempt " + e.attempt;
        patchText = ""; renderPatch("", false);
        break;
      case "token":
        patchText += e.text; renderPatch(patchText, false); break;
      case "gate_result":
        renderPatch(e.diff || patchText, true);
        setStep("ai", "done", "Attempt " + e.attempt + " written.", e.t);
        var row = document.createElement("div"), parts = [];
        Object.keys(e.conditions || {}).forEach(function (k) {
          parts.push('<span class="' + (e.conditions[k] ? "ok" : "no") + '">' +
            (e.conditions[k] ? "✓ " : "✗ ") + (COND[k] || k) + "</span>");
        });
        row.innerHTML = "<b>Try " + e.attempt + ":</b> " + parts.join(" ");
        $("st-chips").appendChild(row);
        if (e.admitted) setStep("gate", "done", "Passed every check. The gate signs it.", e.t);
        else setStep("gate", "active", "Attempt " + e.attempt + " turned down. The failure is sent back to the model.");
        break;
      case "escalated":
        setStep("gate", "failed", "No attempt passed. Handed to a person.", e.t);
        ["deploy", "recovered", "certificate"].forEach(function (s) { setStep(s, "skipped"); });
        setState("gaveup", "Gave up");
        break;
      case "handed_off":
        outcomeEl.textContent = plain(e.reason); break;
      case "deploying":
        setStep("deploy", "active"); break;
      case "deployed":
        setStep("deploy", "done", "Deployed, and the service restarted on the fixed code.", e.t);
        setStep("recovered", "active"); break;
      case "certificate":
        setStep("certificate", "done", "Certificate " + String(e.sequence).padStart(6, "0") +
          ": " + e.replays + ", " + e.attempts + " attempt" + (e.attempts === 1 ? "" : "s") + ".", e.t);
        sealSvg(e.hash).then(function (svg) {
          stamp.innerHTML = svg + "<b>Certificate " + String(e.sequence).padStart(6, "0") + "</b><span>" +
            e.hash.slice(0, 12) + "</span>";
          stamp.hidden = false;
        });
        certLink = "/live-certificates/" + e.sequence;
        addSeam();
        break;
      case "probe":
        if (e.passed) setStep("recovered", "active", "The failing request works. Watching live traffic...");
        break;
      case "recovered":
        redoT = e.mttr;
        setStep("recovered", "done", "Working again " + e.mttr.toFixed(1) + " s after the first error.", e.t);
        setState("mended", "Mended in " + e.mttr.toFixed(1) + " s");
        outcomeEl.textContent = "Mended in " + e.mttr.toFixed(1) + " s, and a new gold seam marks the repair. ";
        if (certLink && !replaying) {
          var cl = document.createElement("a"); cl.href = certLink; cl.textContent = "See its certificate";
          outcomeEl.appendChild(cl);
        }
        break;
      case "explanation":
        var ex = document.getElementById("live-explain");
        if (ex) { ex.hidden = false; ex.querySelector("p").textContent = e.text; }
        break;
      case "pull_request":
        if (e.url) {
          var pr = document.createElement("a");
          pr.href = e.url; pr.textContent = "Pull request #" + e.number + " opened for review";
          pr.target = "_blank"; pr.rel = "noopener";
          outcomeEl.appendChild(document.createElement("br")); outcomeEl.appendChild(pr);
        }
        break;
      case "false_fix":
        setState("broken", "Bad fix"); setStep("recovered", "failed", "The error is still there.", e.t); break;
      case "no_fire":
        setState("", "Bug never fired"); break;
      case "error":
        setState("broken", "Run error");
        outcomeEl.textContent = "The run hit an error: " + e.message; break;
      case "undo_start":
        setState("broken", "Undoing"); outcomeEl.textContent = "Taking the fix out again…"; break;
      case "undo_done":
        setState("broken", "Fix undone");
        outcomeEl.textContent = "The fix is out and the bug is back. Press Redo to put the certified fix back, with no AI.";
        break;
      case "redo_start":
        setState("mending", "Redoing"); outcomeEl.textContent = "Checking the certificate before redeploying…"; break;
      case "cert_verified":
        outcomeEl.textContent = (e.hash && e.chain && e.record && e.patch) ?
          "Certificate verified. Redeploying the exact signed fix…" : "The certificate did not verify, so nothing is deployed.";
        break;
      case "end":
        ended = true; finish();
        if (!replaying && e.undo) { keptRun = currentRun; showUndo("undo"); armKeep(); }
        else if (e.outcome === "undone") showUndo("redo");
        else if (e.outcome === "redone") { showUndo("undo");
          setState("mended", "Mended again"); outcomeEl.textContent = "The certified fix is back in " + (redoT || 0).toFixed(1) + " s, with no AI call. Undo and redo as often as you like."; }
        break;
    }
  }

  function finish() {
    if (source) { source.close(); source = null; }
    go.disabled = false; pick.disabled = false;
    if (replayBtn) replayBtn.disabled = false;
    go.textContent = canLive ? "Break it again" : "Watch another replay";
  }

  function listen(url) {
    reset();
    replaying = false;
    go.disabled = true; pick.disabled = true;
    if (replayBtn) replayBtn.disabled = true;
    source = new EventSource(url);
    source.onmessage = function (m) { handle(JSON.parse(m.data)); };
    source.onerror = function () {
      if (!ended) { setState("broken", "Connection lost"); ended = true; }
      finish();
    };
  }

  function listenMore(url) {          // undo/redo: same picture, new events
    ended = false; lastT = 0; lastWall = performance.now(); samples = [];
    source = new EventSource(url);
    source.onmessage = function (m) { handle(JSON.parse(m.data)); };
    source.onerror = function () { ended = true; finish(); };
  }

  function replay(bug) {
    var name = recordings[bug];
    if (!name) {
      modeEl.textContent = "There is no recording of bug " + bug + " yet.";
      return;
    }
    listen("/live/replay/" + encodeURIComponent(name));
  }

  go.addEventListener("click", function () {
    var bug = pick.value;
    if (!canLive) { replay(bug); return; }
    modeEl.classList.remove("replaying");
    modeEl.textContent = "Live: running the real platform and the real AI model on bug " + bug + ".";
    go.disabled = true;
    fetch("/live/run", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
                         body: "csrf=" + encodeURIComponent(csrf) + "&entry=" + encodeURIComponent(bug) })
      .then(function (r) { return r.json().then(function (j) { return [r.ok, j]; }); })
      .then(function (res) {
        if (!res[0]) { modeEl.textContent = res[1].error || "The run could not start."; go.disabled = false; return; }
        currentRun = res[1].run; showUndo(null);
        listen("/live/stream/" + res[1].run);
      })
      .catch(function () { modeEl.textContent = "The run could not start."; go.disabled = false; });
  });
  if (replayBtn) replayBtn.addEventListener("click", function () { replay(pick.value); });
  // /live?replay=<recording> plays one specific recorded run (from an account page)
  var asked = new URLSearchParams(location.search).get("replay");
  if (asked && /^[0-9A-Za-z-]+$/.test(asked)) listen("/live/replay/" + encodeURIComponent(asked));
  pick.addEventListener("change", function () { go.textContent = canLive ? "Break it" : "Watch a replay"; });
})();
