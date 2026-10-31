(function () {
  var T = window.__TAURI__;
  var es = (navigator.language || "").toLowerCase().indexOf("es") === 0;
  var tx = es
    ? { graph: "grafo", recent: "cambios", none: "Sin cambios recientes", bell: "Notificaciones", empty: "El grafo aparece cuando el dashboard está listo.", open: "Abrir el grafo" }
    : { graph: "graph", recent: "changes", none: "No recent changes", bell: "Notifications", empty: "The graph shows up once the dashboard is ready.", open: "Open the graph" };
  document.documentElement.lang = es ? "es" : "en";

  var root = document.documentElement;
  var island = document.getElementById("island");
  var bell = document.getElementById("bell");
  var count = document.getElementById("count");
  var sub = document.getElementById("sub");
  bell.setAttribute("aria-label", tx.bell);

  var info = { os: "", notch: false, notch_w: 190, header: 34, alert: [352, 98], graph: [500, 334] };
  var mode = "";          // "" while closed, then "alert" or "graph"
  var lines = [];
  var graph = null;
  var timer = null;
  var hover = false;
  var raf = 0;

  function invoke(cmd, args) {
    if (!T) return Promise.resolve(null);
    return T.core.invoke(cmd, args || {});
  }

  // ---------- sizing and the open / close animation (the transitions are in notch.css) ----------
  function applyInfo(next) {
    if (next) {
      ["os", "notch", "notch_w", "header", "alert", "graph"].forEach(function (k) { if (next[k] !== undefined) info[k] = next[k]; });
    }
    root.dataset.os = info.os || "";
    root.dataset.notch = info.notch ? "1" : "0";
    island.style.setProperty("--header", info.header + "px");
    island.style.setProperty("--notch-w", info.notch_w + "px");
    island.style.setProperty("--seed-w", (info.notch ? info.notch_w : 150) + "px");
    island.style.setProperty("--seed-h", (info.notch ? info.header : 0) + "px");
  }

  function sizeFor(m) {
    var s = info[m] || info.graph;
    island.style.setProperty("--w", s[0] + "px");
    island.style.setProperty("--h", s[1] + "px");
  }

  function open(next) {
    var fresh = !mode;
    if (fresh) {
      // start from the notch (or from nothing) without animating, then let it grow
      island.classList.add("seed", "no-anim");
      void island.offsetWidth;
      island.classList.remove("no-anim");
    }
    mode = next;
    sub.textContent = mode === "graph" ? tx.graph : tx.recent;
    sizeFor(mode);
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        island.classList.remove("seed");
        island.dataset.mode = mode;
        if (mode === "graph") loadGraph();
        else stopGraph();
      });
    });
    arm();
  }

  function close() {
    if (!mode) return;
    mode = "";
    clearTimeout(timer);
    delete island.dataset.mode;
    island.classList.add("seed");
    setTimeout(stopGraph, 640);
  }

  function arm(ms) {
    clearTimeout(timer);
    if (!mode) return;
    var wait = ms || (mode === "alert" ? 6500 : 14000);
    timer = setTimeout(function () {
      if (hover) arm(2500);
      else invoke("hide_notch");
    }, wait);
  }

  // ---------- alert ----------
  function renderLines(items) {
    lines = items || [];
    var ul = document.getElementById("list");
    ul.textContent = "";
    var shown = lines.length ? lines.slice(0, 2) : [null];
    shown.forEach(function (l) {
      var li = document.createElement("li");
      if (l === null) {
        li.className = "empty";
        li.textContent = tx.none;
      } else {
        // "Changed: path" → verb + path. A line with no verb ("3 changes in your vault",
        // "Updating the app…") is all text: no empty verb column left as a gap.
        var m = /^([^:\n]{1,16}):\s+(.+)$/.exec(l);
        var v = document.createElement("span");
        var p = document.createElement("span");
        p.className = "p";
        if (m) { v.className = "v"; v.textContent = m[1]; p.textContent = m[2]; }
        else { li.className = "plain"; p.textContent = l; }
        if (m) li.appendChild(v);
        li.appendChild(p);
      }
      ul.appendChild(li);
    });
    count.hidden = !lines.length;
    count.textContent = lines.length > 9 ? "9+" : String(lines.length);
  }

  function ringBell() {
    bell.classList.remove("ring");
    void bell.offsetWidth;
    bell.classList.add("ring");
  }

  var alertEl = document.getElementById("alert");
  alertEl.onclick = function () { invoke("notch_open", { mode: "graph" }); };
  alertEl.onkeydown = function (e) { if (e.key === "Enter" || e.key === " ") invoke("notch_open", { mode: "graph" }); };
  bell.onclick = function (e) {
    e.stopPropagation();
    invoke("notch_open", { mode: mode === "graph" ? "alert" : "graph" });
  };
  island.addEventListener("mouseenter", function () { hover = true; });
  island.addEventListener("mouseleave", function () { hover = false; arm(mode === "alert" ? 2200 : 3500); });
  island.addEventListener("mousemove", function () { if (mode === "graph") arm(); });
  addEventListener("keydown", function (e) { if (e.key === "Escape") invoke("hide_notch"); });

  // ---------- graph ----------
  var KIND = {
    project: { r: 7, fill: "#f4f4f0", stroke: "#f4f4f0" },
    subproject: { r: 5, fill: "none", stroke: "#f4f4f0" },
    profile: { r: 6.5, fill: "#f4f4f0", stroke: "#f4f4f0" },
    memory: { r: 4, fill: "none", stroke: "#c8c8c2" },
    skill: { r: 4.5, fill: "#d6d6d0", stroke: "#d6d6d0" },
    source: { r: 3.5, fill: "none", stroke: "#8a8a84" },
    file: { r: 3.2, fill: "#8a8a84", stroke: "#8a8a84" },
    note: { r: 3.2, fill: "#6e6e68", stroke: "#6e6e68" }
  };
  var NS = "http://www.w3.org/2000/svg";
  var scene = null;

  function layout(nodes, w, h) {
    var cx = w / 2, cy = h / 2 - 4;
    var R = Math.min(w * 0.4, h * 0.42);
    nodes.forEach(function (n, i) {
      var a = -Math.PI / 2 + (i / Math.max(nodes.length, 1)) * Math.PI * 2;
      var inner = n.kind === "file" || n.kind === "note" || n.kind === "source" || n.kind === "memory";
      var rr = inner ? R * 0.6 : R;
      n.x = cx + Math.cos(a) * rr * 1.18;
      n.y = cy + Math.sin(a) * rr * 0.9;
    });
    for (var iter = 0; iter < 28; iter++) {
      for (var i = 0; i < nodes.length; i++) {
        for (var j = i + 1; j < nodes.length; j++) {
          var a = nodes[i], b = nodes[j];
          var dx = b.x - a.x, dy = b.y - a.y;
          var d2 = dx * dx + dy * dy || 0.01;
          if (d2 < 34 * 34) {
            var d = Math.sqrt(d2);
            var push = (34 - d) / d * 0.35;
            a.x -= dx * push; a.y -= dy * push;
            b.x += dx * push; b.y += dy * push;
          }
        }
      }
    }
    nodes.forEach(function (n) {
      n.x = Math.max(24, Math.min(w - 24, n.x));
      n.y = Math.max(18, Math.min(h - 30, n.y));
      n.bx = n.x; n.by = n.y;
    });
  }

  function el(name, attrs) {
    var e = document.createElementNS(NS, name);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }

  function stopGraph() {
    cancelAnimationFrame(raf);
    raf = 0;
    scene = null;
  }

  function draw(data) {
    graph = data;
    stopGraph();
    var svg = document.getElementById("svg");
    var tip = document.getElementById("tip");
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    var nodes = (data && data.nodes) || [];
    var links = (data && data.links) || [];
    if (!nodes.length) {
      tip.textContent = tx.empty;
      tip.className = "on";
      return;
    }
    tip.className = "";
    // lay out for the size the island is growing to, not the one it has right now
    var w = info.graph[0], h = info.graph[1] - info.header;
    layout(nodes, w, h);
    var byId = {};
    nodes.forEach(function (n, i) { byId[n.id] = n; n.i = i; });
    var hit = lines.join(" ").toLowerCase();

    var linkEls = [];
    links.forEach(function (l) {
      var a = byId[l.source], b = byId[l.target];
      if (!a || !b) return;
      var line = el("line", { "class": "link" });
      svg.appendChild(line);
      linkEls.push({ a: a, b: b, el: line });
    });
    nodes.forEach(function (n) {
      var st = KIND[n.kind] || KIND.note;
      n.st = st;
      n.hot = !!(hit && n.path && hit.indexOf(String(n.path).toLowerCase()) >= 0);
      var g = el("g", { "class": "node" });
      n.core = el("circle", { "class": "core", r: 0, fill: st.fill, stroke: st.stroke, "stroke-width": 1.25 });
      g.appendChild(n.core);
      if (n.hot) { n.halo = el("circle", { "class": "halo", r: 0 }); g.appendChild(n.halo); }
      if (n.kind === "project" || n.kind === "profile") {
        n.text = el("text", { "class": "label" });
        n.text.textContent = n.label;
        g.appendChild(n.text);
      }
      g.onmouseenter = function () { tip.textContent = n.label + " · " + n.kind; tip.className = "on"; };
      g.onmouseleave = function () { tip.className = ""; };
      g.onclick = function (e) { e.stopPropagation(); invoke("open_view", { view: "grafo" }); invoke("hide_notch"); };
      svg.appendChild(g);
    });
    var spark = el("circle", { "class": "spark", r: 0 });
    svg.appendChild(spark);

    scene = { nodes: nodes, links: linkEls, spark: spark, start: performance.now(), pulse: null };
    raf = requestAnimationFrame(frame);
  }

  var clamp = function (x) { return Math.max(0, Math.min(1, x)); };
  var easeOut = function (t) { return 1 - Math.pow(1 - t, 3); };

  // one loop for the pop-in, the slow drift, the pulses on what just changed and a spark running along a link
  function frame(now) {
    if (!scene) return;
    var t = (now - scene.start) / 1000;
    scene.nodes.forEach(function (n) {
      var pop = easeOut(clamp((t - 0.12 - n.i * 0.022) / 0.5));
      n.x = n.bx + Math.sin(t * 0.7 + n.i) * 1.4;
      n.y = n.by + Math.cos(t * 0.55 + n.i * 0.6) * 1.2;
      n.pop = pop;
      n.core.setAttribute("cx", n.x); n.core.setAttribute("cy", n.y);
      n.core.setAttribute("r", n.st.r * pop * (n.hot ? 1.25 : 1));
      if (n.halo) {
        var ph = (t * 0.9 + n.i * 0.3) % 1;
        n.halo.setAttribute("cx", n.x); n.halo.setAttribute("cy", n.y);
        n.halo.setAttribute("r", n.st.r + 2 + ph * 9);
        n.halo.setAttribute("opacity", (1 - ph) * 0.7 * pop);
      }
      if (n.text) { n.text.setAttribute("x", n.x + n.st.r + 4); n.text.setAttribute("y", n.y + 3.5); n.text.setAttribute("opacity", pop); }
    });
    scene.links.forEach(function (l) {
      var o = Math.min(l.a.pop, l.b.pop);
      l.el.setAttribute("x1", l.a.x); l.el.setAttribute("y1", l.a.y);
      l.el.setAttribute("x2", l.b.x); l.el.setAttribute("y2", l.b.y);
      l.el.setAttribute("opacity", o);
    });
    // a spark travels along a random link every ~1.8 s
    if (scene.links.length && t > 0.9) {
      if (!scene.pulse || now - scene.pulse.at > 900) {
        if (!scene.pulse || now - scene.pulse.at > 1800) {
          scene.pulse = { at: now, l: scene.links[Math.floor(Math.random() * scene.links.length)], flip: Math.random() < 0.5 };
        }
      }
      var p = scene.pulse, k = (now - p.at) / 900;
      if (p && k >= 0 && k <= 1) {
        var f = easeOut(k), from = p.flip ? p.l.b : p.l.a, to = p.flip ? p.l.a : p.l.b;
        scene.spark.setAttribute("cx", from.x + (to.x - from.x) * f);
        scene.spark.setAttribute("cy", from.y + (to.y - from.y) * f);
        scene.spark.setAttribute("r", 1.8 * Math.sin(Math.PI * k) + 0.2);
      } else {
        scene.spark.setAttribute("r", 0);
      }
    }
    raf = requestAnimationFrame(frame);
  }

  function loadGraph() {
    invoke("notch_graph").then(function (g) { if (mode === "graph" && g) draw(g); }).catch(function () { if (mode === "graph") draw({ nodes: [], links: [] }); });
  }

  // ---------- events from the app ----------
  function onShow(p) {
    p = p || {};
    applyInfo(p);
    var before = lines.join("|");
    renderLines(p.lines || []);
    var next = p.mode === "alert" ? "alert" : "graph";
    if (mode === next && !(next === "alert" && before !== lines.join("|"))) { arm(); return; }
    var wasClosed = !mode;
    open(next);
    if (next === "alert" || wasClosed) setTimeout(ringBell, 260);
  }

  function preview() {
    // opened in a browser, for looking at the design: notch.html?mode=graph|alert&os=macos|windows&notch=1
    var q = new URLSearchParams(location.search);
    document.body.classList.add("preview");
    var os = q.get("os") || "macos";
    var notch = q.get("notch") === "1";
    var header = notch ? 32 : 34;
    onShow({
      os: os, notch: notch, notch_w: 190, header: header,
      alert: [352, header + 64], graph: [500, header + 300],
      mode: q.get("mode") === "alert" ? "alert" : "graph",
      lines: ["Changed: projects/brain-project/historia.md", "New: projects/brain-project/notch.md"]
    });
    if (q.get("mode") !== "alert") {
      setTimeout(function () {
        draw({
          nodes: [
            { id: "p", label: "brain-project", kind: "project", path: "projects/brain-project.md" },
            { id: "h", label: "historia", kind: "file", path: "projects/brain-project/historia.md" },
            { id: "a", label: "arquitectura", kind: "file", path: "projects/brain-project/arquitectura.md" },
            { id: "n", label: "notch", kind: "file", path: "projects/brain-project/notch.md" },
            { id: "m", label: "trabajo", kind: "memory", path: "memory/trabajo.md" },
            { id: "s", label: "organizar", kind: "skill", path: "skills/organizar.md" },
            { id: "u", label: "perfil", kind: "profile", path: "profile.md" },
            { id: "x", label: "docs", kind: "source", path: "knowledge/sources/docs.md" }
          ],
          links: [
            { source: "h", target: "p", kind: "part" }, { source: "a", target: "p", kind: "part" },
            { source: "n", target: "p", kind: "part" }, { source: "m", target: "u", kind: "related" },
            { source: "s", target: "p", kind: "related" }, { source: "x", target: "p", kind: "related" },
            { source: "u", target: "p", kind: "related" }
          ]
        });
      }, 20);
    }
  }

  if (!T) { preview(); return; }

  applyInfo();
  T.event.listen("notch-show", function (e) { onShow(e.payload); });
  T.event.listen("notch-hide", function () { close(); });
  invoke("notch_state").then(function (s) {
    if (!s) return;
    applyInfo(s);
    renderLines(s.lines || []);
    if (s.mode) onShow(s);
  }).catch(function () {});
})();
