// Kintsugi console: progressive enhancement only. Every control here is a
// real link or form that works without JavaScript; this file makes them
// faster and adds the tooltip, keyboard grid and in-page dialogs.
(function () {
  "use strict";
  var root = document.documentElement;

  // ---------------------------------------------------------------- theme
  function currentTheme() {
    return root.getAttribute("data-theme") === "light" ? "light" : "dark";
  }
  function labelTheme() {
    var btn = document.querySelector("[data-theme-toggle]");
    if (!btn) return;
    var next = currentTheme() === "dark" ? "light" : "dark";
    btn.setAttribute("aria-label", "Switch to " + next + " theme");
    var text = btn.querySelector(".theme-text");
    if (text) text.textContent = next === "light" ? "Light" : "Dark";
  }
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-theme-toggle]");
    if (!btn) return;
    var next = currentTheme() === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try { localStorage.setItem("kintsugi-theme", next); } catch (err) {}
    labelTheme();
  });
  labelTheme();

  // ---------------------------------------------------------- footer seam
  var seam = document.querySelector(".foot .seam");
  if (seam) {
    var draw = function () { seam.classList.add("drawn"); };
    if ("IntersectionObserver" in window) {
      var io = new IntersectionObserver(function (entries) {
        if (entries.some(function (e) { return e.isIntersecting; })) {
          draw(); io.disconnect();
        }
      }, { threshold: 0.5 });
      io.observe(seam);
    } else { draw(); }
  }

  // --------------------------------------------------------------- dialog
  var dialog = document.getElementById("detail");
  var dialogBody = dialog && dialog.querySelector(".detail-body");
  var dialogTitle = dialog && dialog.querySelector(".detail-title");
  var returnFocus = null;

  function openDetail(url, label, fallbackHref) {
    if (!dialog || typeof dialog.showModal !== "function") {
      window.location.href = fallbackHref; return;
    }
    returnFocus = document.activeElement;
    dialogTitle.textContent = label || "Details";
    dialogBody.setAttribute("aria-busy", "true");
    dialogBody.innerHTML = "<p class='muted'>Loading…</p>";
    dialog.showModal();
    fetch(url, { headers: { "Accept": "text/html" } })
      .then(function (r) {
        if (!r.ok) throw new Error(r.status);
        return r.text();
      })
      .then(function (html) {
        dialogBody.innerHTML = html;
        dialogBody.removeAttribute("aria-busy");
        var h = dialogBody.querySelector("h2");
        if (h) { h.setAttribute("tabindex", "-1"); h.focus(); }
      })
      .catch(function () {
        dialogBody.removeAttribute("aria-busy");
        dialogBody.innerHTML = "<p>This could not be loaded. " +
          "<a href='" + fallbackHref + "'>Open it as a page instead</a>.</p>";
      });
  }
  if (dialog) {
    dialog.querySelector(".close").addEventListener("click", function () {
      dialog.close();
    });
    dialog.addEventListener("click", function (e) {
      if (e.target === dialog) dialog.close();       // backdrop click
    });
    dialog.addEventListener("close", function () {
      if (returnFocus && document.contains(returnFocus)) returnFocus.focus();
    });
  }
  document.addEventListener("click", function (e) {
    var a = e.target.closest("a[data-detail]");
    if (!a || e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;
    e.preventDefault();
    hideTip();
    openDetail(a.getAttribute("data-detail"), a.getAttribute("data-label"),
               a.getAttribute("href"));
  });

  // ------------------------------------------------------- matrix tooltip
  var tip = document.createElement("div");
  tip.className = "tip"; tip.setAttribute("role", "tooltip"); tip.id = "mx-tip";
  document.body.appendChild(tip);

  function showTip(cell) {
    var d = cell.dataset;
    tip.textContent = "";
    var head = document.createElement("b");
    head.textContent = d.bug + ": " + d.title;
    tip.appendChild(head);
    var meta = document.createElement("div");
    meta.className = "faint";
    meta.textContent = d.approach + ". " + d.expected + ".";
    tip.appendChild(meta);
    var list = document.createElement("ol");
    (d.runs || "").split("|").forEach(function (run, i) {
      var li = document.createElement("li");
      li.textContent = "Run " + (i + 1) + ": " + run;
      list.appendChild(li);
    });
    tip.appendChild(list);
    var r = cell.getBoundingClientRect();
    tip.classList.add("on");
    var w = tip.offsetWidth, h = tip.offsetHeight;
    var x = Math.min(window.innerWidth - w - 12, Math.max(12, r.left + r.width / 2 - w / 2));
    var y = r.top - h - 10;
    if (y < 12) y = r.bottom + 10;
    tip.style.left = x + "px"; tip.style.top = y + "px";
    cell.setAttribute("aria-describedby", "mx-tip");
  }
  function hideTip() {
    tip.classList.remove("on");
    var owner = document.querySelector("[aria-describedby='mx-tip']");
    if (owner) owner.removeAttribute("aria-describedby");
  }

  // ------------------------------------------- matrix keyboard navigation
  var grid = document.querySelector(".mx");
  if (grid) {
    var rows = Array.prototype.map.call(grid.querySelectorAll(".mx-row"),
      function (row) { return Array.prototype.slice.call(row.querySelectorAll(".mx-cell")); });
    var all = [].concat.apply([], rows);
    all.forEach(function (c, i) { c.tabIndex = i === 0 ? 0 : -1; });
    function moveTo(cell) {
      all.forEach(function (c) { c.tabIndex = -1; });
      cell.tabIndex = 0; cell.focus();
    }
    grid.addEventListener("keydown", function (e) {
      var cell = e.target.closest(".mx-cell");
      if (!cell) return;
      var r = -1, c = -1;
      rows.some(function (row, ri) {
        var ci = row.indexOf(cell);
        if (ci >= 0) { r = ri; c = ci; return true; }
        return false;
      });
      var next = null;
      if (e.key === "ArrowRight") next = rows[r][c + 1];
      else if (e.key === "ArrowLeft") next = rows[r][c - 1];
      else if (e.key === "ArrowDown") next = rows[r + 1] && rows[r + 1][c];
      else if (e.key === "ArrowUp") next = rows[r - 1] && rows[r - 1][c];
      else if (e.key === "Home") next = rows[r][0];
      else if (e.key === "End") next = rows[r][rows[r].length - 1];
      else if (e.key === "Escape") { hideTip(); return; }
      else return;
      e.preventDefault();
      if (next) moveTo(next);
    });
    grid.addEventListener("mouseover", function (e) {
      var cell = e.target.closest(".mx-cell");
      if (cell) showTip(cell);
    });
    grid.addEventListener("mouseleave", hideTip);
    grid.addEventListener("focusin", function (e) {
      var cell = e.target.closest(".mx-cell");
      if (cell) { showTip(cell); highlightBug(cell.dataset.bug); }
    });
    grid.addEventListener("focusout", function () { hideTip(); highlightBug(null); });
    grid.addEventListener("mouseover", function (e) {
      var cell = e.target.closest(".mx-cell");
      highlightBug(cell ? cell.dataset.bug : null);
    });
    grid.addEventListener("mouseleave", function () { highlightBug(null); });
    function highlightBug(bug) {
      all.forEach(function (c) {
        c.classList.toggle("is-active", !!bug && c.dataset.bug === bug);
      });
    }
    window.addEventListener("scroll", hideTip, { passive: true });
  }

  // ------------------------------------------------------------- explorer
  var form = document.getElementById("ex-form");
  var results = document.getElementById("ex-results");
  if (form && results) {
    var timer = null, seq = 0;
    function paramsFromForm() {
      var p = new URLSearchParams();
      new FormData(form).forEach(function (v, k) {
        if (v !== "" && !(k === "sort" && v === "bug") &&
            !(k === "order" && v === "asc") && !(k === "page" && v === "1"))
          p.set(k, v);
      });
      return p;
    }
    function load(params, push) {
      var mine = ++seq;
      results.setAttribute("aria-busy", "true");
      fetch("/fragment/explorer?" + params.toString())
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
        .then(function (html) {
          if (mine !== seq) return;               // a newer request won
          results.innerHTML = html;
          results.removeAttribute("aria-busy");
          var qs = params.toString();
          var url = window.location.pathname + (qs ? "?" + qs : "") + "#tests";
          history[push ? "pushState" : "replaceState"](null, "", url);
          syncHidden(params);
        })
        .catch(function () {
          results.removeAttribute("aria-busy");
          form.submit();                           // fall back to a full load
        });
    }
    // the form always mirrors the URL, so back/forward and reset links agree
    function syncHidden(params) {
      var defaults = { sort: "bug", order: "asc" };
      ["bug", "approach", "outcome", "run", "sort", "order"].forEach(function (k) {
        var el = form.elements[k];
        if (el && el !== document.activeElement)
          el.value = params.get(k) || defaults[k] || "";
      });
    }
    form.addEventListener("submit", function (e) {
      e.preventDefault(); load(paramsFromForm(), true);
    });
    form.addEventListener("change", function (e) {
      if (e.target.name === "bug") return;
      load(paramsFromForm(), false);
    });
    form.addEventListener("input", function (e) {
      if (e.target.name !== "bug") return;
      clearTimeout(timer);
      timer = setTimeout(function () { load(paramsFromForm(), false); }, 250);
    });
    results.addEventListener("click", function (e) {
      var a = e.target.closest("a[data-ex]");
      if (!a || e.metaKey || e.ctrlKey) return;
      e.preventDefault();
      var url = new URL(a.href, window.location.href);
      load(url.searchParams, true);
      if (a.hasAttribute("data-ex-top")) {
        document.getElementById("tests").scrollIntoView({ block: "start" });
      }
    });
    document.addEventListener("click", function (e) {
      var r = e.target.closest("[data-ex-reset]");
      if (!r) return;
      e.preventDefault();
      if (document.activeElement) document.activeElement.blur();
      load(new URLSearchParams(), true);
    });
    window.addEventListener("popstate", function () {
      load(new URLSearchParams(window.location.search), false);
    });
  }
})();
