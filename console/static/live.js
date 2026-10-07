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

  var patchText = "";
  function handle(e) {
    if (e.t !== undefined && e.kind !== "replay") { lastT = e.t; lastWall = performance.now(); }
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
        addSeam();
        break;
      case "probe":
        if (e.passed) setStep("recovered", "active", "The failing request works. Watching live traffic...");
        break;
      case "recovered":
        setStep("recovered", "done", "Working again " + e.mttr.toFixed(1) + " s after the first error.", e.t);
        setState("mended", "Mended in " + e.mttr.toFixed(1) + " s");
        outcomeEl.textContent = "Mended in " + e.mttr.toFixed(1) + " s, and a new gold seam marks the repair.";
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
      case "end":
        ended = true; finish(); break;
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
