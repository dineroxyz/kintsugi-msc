// Home: the hero's product window replays a recorded real run (from the
// same event stream the Live view uses), and the pipeline's seam draws
// itself once when it scrolls into view.
(function () {
  "use strict";
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ------------------------------------------------------- pipeline seam
  var pipe = document.querySelector(".pipe-wrap");
  if (pipe) {
    if (reduce || !("IntersectionObserver" in window)) pipe.classList.add("drawn");
    else {
      var po = new IntersectionObserver(function (es) {
        if (es.some(function (e) { return e.isIntersecting; })) { pipe.classList.add("drawn"); po.disconnect(); }
      }, { threshold: 0.4 });
      po.observe(pipe);
    }
  }

  // ------------------------------------------------------ product window
  var win = document.querySelector(".window[data-replay]");
  if (!win) return;
  var canvas = win.querySelector(".window-traffic");
  var stateEl = win.querySelector("[data-state]");
  var sealEl = win.querySelector("[data-seal]");
  var steps = Array.prototype.slice.call(win.querySelectorAll(".window-steps li"));
  var samples = [], lanes = {}, lastT = 0, lastWall = 0, running = false, visible = false;
  var source = null;

  function css(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
  function setState(cls, text) { stateEl.className = "window-state " + cls; stateEl.textContent = text; }
  function mark(kind) {
    var idx = steps.findIndex(function (li) { return li.dataset.s === kind; });
    if (idx < 0) return;
    steps.forEach(function (li, i) {
      li.classList.toggle("on", i <= idx);
      li.classList.toggle("now", i === idx + 1);
    });
  }
  function reset() {
    samples = []; lanes = {}; lastT = 0; lastWall = performance.now();
    steps.forEach(function (li) { li.className = ""; });
    sealEl.hidden = true; sealEl.innerHTML = "";
    setState("", "Healthy");
  }

  function seal(hash) {
    return crypto.subtle.digest("SHA-256", new TextEncoder().encode(hash)).then(function (buf) {
      var bits = "";
      new Uint8Array(buf).forEach(function (b) { bits += b.toString(2).padStart(8, "0"); });
      var cells = "", k = 0;
      for (var r = 0; r < 5; r++) for (var c = 0; c < 3; c++, k++) {
        if (bits[k] !== "1") continue;
        (c === 2 ? [2] : [c, 4 - c]).forEach(function (x) {
          cells += '<rect x="' + (6 + x * 5.6).toFixed(1) + '" y="' + (6 + r * 5.6).toFixed(1) + '" width="5.6" height="5.6"/>';
        });
      }
      return '<svg width="64" height="64" viewBox="0 0 40 40" aria-hidden="true"><rect x="1.5" y="1.5" width="37" height="37" rx="3" fill="none" stroke="var(--broken)" stroke-width="2.4"/><g fill="var(--broken)">' + cells + "</g></svg>";
    }).catch(function () { return ""; });
  }

  function handle(e) {
    if (e.t !== undefined && e.kind !== "replay") { lastT = e.t; lastWall = performance.now(); }
    switch (e.kind) {
      case "start": reset(); break;
      case "traffic":
        e.samples.forEach(function (s) {
          if (!(s[2] in lanes)) lanes[s[2]] = Object.keys(lanes).length;
          samples.push([s[0], s[1], s[2], Math.random() * 2 - 1]);
        });
        if (samples.length > 4000) samples = samples.slice(-3000);
        break;
      case "fault_deployed": mark(e.kind); setState("broken", "Broken"); break;
      case "captured": mark(e.kind); break;
      case "reproduce_done": mark(e.kind); setState("mending", "Mending"); break;
      case "gate_result": if (e.admitted) mark(e.kind); break;
      case "deployed": mark(e.kind); break;
      case "recovered": mark(e.kind); setState("mended", "Mended in " + e.mttr.toFixed(1) + " s"); break;
      case "certificate":
        mark(e.kind);
        seal(e.hash).then(function (svg) { sealEl.innerHTML = svg; sealEl.hidden = false; });
        break;
      case "end": finish(); break;
    }
  }

  function finish() {
    if (source) { source.close(); source = null; }
    running = false;
    steps.forEach(function (li) { li.classList.remove("now"); });
    setTimeout(function () { if (visible) play(); }, 6000);   // loop while on screen
  }

  function play() {
    if (running) return;
    running = true; reset();
    source = new EventSource("/live/replay/" + encodeURIComponent(win.dataset.replay));
    source.onmessage = function (m) { handle(JSON.parse(m.data)); };
    source.onerror = function () { if (source) source.close(); source = null; running = false; };
  }

  function draw() {
    var r = canvas.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    var w = Math.round(r.width * dpr), h = Math.round(r.height * dpr);
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w; canvas.height = h;
      canvas.getContext("2d").setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    var ctx = canvas.getContext("2d"), t = lastT + (running ? (performance.now() - lastWall) / 1000 : 0);
    ctx.clearRect(0, 0, r.width, r.height);
    var ok = css("--fixed"), bad = css("--broken"), n = Math.max(Object.keys(lanes).length, 1), WINDOW = 10;
    for (var i = samples.length - 1; i >= 0; i--) {
      var s = samples[i], age = t - s[0];
      if (age > WINDOW) break;
      if (age < 0) continue;
      var x = r.width - (age / WINDOW) * r.width;
      var y = 16 + ((lanes[s[2]] + 0.5) / n) * (r.height - 32) + s[3] * 9;
      ctx.globalAlpha = Math.max(0.25, 1 - age / WINDOW);
      ctx.fillStyle = s[1] >= 500 ? bad : ok;
      ctx.beginPath(); ctx.arc(x, y, s[1] >= 500 ? 3.2 : 2.2, 0, Math.PI * 2); ctx.fill();
    }
    ctx.globalAlpha = 1;
    requestAnimationFrame(draw);
  }

  if (reduce) {                         // no motion: show where the run ends
    steps.forEach(function (li) { li.classList.add("on"); });
    setState("mended", "Mended");
    return;
  }
  requestAnimationFrame(draw);
  if ("IntersectionObserver" in window) {
    new IntersectionObserver(function (es) {
      visible = es.some(function (e) { return e.isIntersecting; });
      if (visible) play();
    }, { threshold: 0.3 }).observe(win);
  } else { visible = true; play(); }
})();
