(function () {
  var T = window.__TAURI__;
  var es = (navigator.language || "").toLowerCase().indexOf("es") === 0;
  var tx = es
    ? { sub: "grafo", none: "Sin cambios recientes", bell: "Notificaciones", empty: "El grafo aparece cuando el dashboard está listo.", open: "Abrir en el grafo" }
    : { sub: "graph", none: "No recent changes", bell: "Notifications", empty: "The graph shows up once the dashboard is ready.", open: "Open in the graph" };
  document.documentElement.lang = es ? "es" : "en";
  document.getElementById("sub").textContent = tx.sub;
  document.getElementById("bell").setAttribute("aria-label", tx.bell);

  var mode = "graph";
  var lines = [];
  var graph = null;
  var timer = null;
  var hover = false;

  function invoke(cmd, args) {
    if (!T) return Promise.resolve(null);
    return T.core.invoke(cmd, args || {});
  }

  function renderLines(items) {
    lines = items || [];
    var ul = document.getElementById("list");
    ul.textContent = "";
    var shown = lines.length ? lines.slice(0, 2) : [null];
    shown.forEach(function (l) {
      var li = document.createElement("li");
      li.textContent = l === null ? tx.none : l;
      if (l === null) li.className = "empty";
      ul.appendChild(li);
    });
    document.getElementById("dot").hidden = !lines.length;
  }

  function setMode(next) {
    mode = next === "alert" ? "alert" : "graph";
    document.body.classList.remove("mode-alert", "mode-graph");
    document.body.classList.add(mode === "alert" ? "mode-alert" : "mode-graph");
    clearTimeout(timer);
    if (mode === "alert") arm();
    else loadGraph();
  }

  function arm() {
    clearTimeout(timer);
    timer = setTimeout(function () { if (hover) arm(); else invoke("hide_notch"); }, 7000);
  }

  document.getElementById("alert").addEventListener("mouseenter", function () { hover = true; });
  document.getElementById("alert").addEventListener("mouseleave", function () { hover = false; if (mode === "alert") arm(); });
  document.getElementById("alert").onclick = function () { invoke("notch_open", { mode: "graph" }); };
  document.getElementById("bell").onclick = function (e) {
    e.stopPropagation();
    invoke("notch_open", { mode: "alert" });
  };
  addEventListener("keydown", function (e) { if (e.key === "Escape") invoke("hide_notch"); });

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

  function layout(nodes, links, w, h) {
    var cx = w / 2, cy = (h / 2) + 2;
    var R = Math.min(w * 0.36, h * 0.38);
    nodes.forEach(function (n, i) {
      var a = -Math.PI / 2 + (i / Math.max(nodes.length, 1)) * Math.PI * 2;
      var inner = n.kind === "file" || n.kind === "note" || n.kind === "source" || n.kind === "memory";
      var rr = inner ? R * 0.62 : R;
      n.x = cx + Math.cos(a) * rr;
      n.y = cy + Math.sin(a) * rr * 0.9;
    });
    for (var iter = 0; iter < 24; iter++) {
      for (var i = 0; i < nodes.length; i++) {
        for (var j = i + 1; j < nodes.length; j++) {
          var a = nodes[i], b = nodes[j];
          var dx = b.x - a.x, dy = b.y - a.y;
          var d2 = dx * dx + dy * dy || 0.01;
          var min = 36 * 36;
          if (d2 < min) {
            var d = Math.sqrt(d2);
            var push = (36 - d) / d * 0.35;
            a.x -= dx * push; a.y -= dy * push;
            b.x += dx * push; b.y += dy * push;
          }
        }
      }
    }
    nodes.forEach(function (n) {
      n.x = Math.max(22, Math.min(w - 22, n.x));
      n.y = Math.max(16, Math.min(h - 28, n.y));
      n.bx = n.x; n.by = n.y;
    });
    return links;
  }

  function draw(data) {
    graph = data;
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
    var w = svg.clientWidth || 440, h = svg.clientHeight || 250;
    layout(nodes, links, w, h);
    var ns = "http://www.w3.org/2000/svg";
    var byId = {};
    nodes.forEach(function (n) { byId[n.id] = n; });
    var hit = (lines[0] || "").toLowerCase();
    links.forEach(function (l) {
      var a = byId[l.source], b = byId[l.target];
      if (!a || !b) return;
      var line = document.createElementNS(ns, "line");
      line.setAttribute("class", "link");
      line.setAttribute("x1", a.x); line.setAttribute("y1", a.y);
      line.setAttribute("x2", b.x); line.setAttribute("y2", b.y);
      svg.appendChild(line);
    });
    nodes.forEach(function (n, i) {
      var st = KIND[n.kind] || KIND.note;
      var g = document.createElementNS(ns, "g");
      g.setAttribute("class", "node");
      var c = document.createElementNS(ns, "circle");
      c.setAttribute("cx", n.x); c.setAttribute("cy", n.y); c.setAttribute("r", 0);
      c.setAttribute("fill", st.fill); c.setAttribute("stroke", st.stroke); c.setAttribute("stroke-width", "1.25");
      g.appendChild(c);
      if (n.kind === "project" || n.kind === "profile") {
        var t = document.createElementNS(ns, "text");
        t.setAttribute("class", "label");
        t.setAttribute("x", n.x + 9); t.setAttribute("y", n.y + 3);
        t.textContent = n.label;
        g.appendChild(t);
      }
      g.onmouseenter = function () { tip.textContent = n.label + " · " + n.kind; tip.className = "on"; };
      g.onmouseleave = function () { tip.className = ""; };
      g.ondblclick = function () { invoke("open_view", { view: "grafo" }); invoke("hide_notch"); };
      svg.appendChild(g);
      var hot = hit && n.path && hit.indexOf(String(n.path).toLowerCase()) >= 0;
      setTimeout(function () { c.setAttribute("r", hot ? st.r + 1.5 : st.r); }, 20 + i * 12);
    });
    drift(nodes, links, svg);
  }

  var driftTimer = 0;
  function drift(nodes, links, svg) {
    cancelAnimationFrame(driftTimer);
    var started = performance.now();
    function tick(now) {
      if (mode !== "graph") return;
      var t = (now - started) / 1000;
      var linesEl = svg.querySelectorAll(".link");
      var circles = svg.querySelectorAll(".node circle");
      nodes.forEach(function (n, i) {
        var x = n.bx + Math.sin(t * 0.7 + i) * 1.3;
        var y = n.by + Math.cos(t * 0.55 + i * 0.6) * 1.1;
        n.x = x; n.y = y;
        if (circles[i]) { circles[i].setAttribute("cx", x); circles[i].setAttribute("cy", y); }
      });
      var li = 0;
      links.forEach(function (l) {
        var a = nodes.filter(function (n) { return n.id === l.source; })[0];
        var b = nodes.filter(function (n) { return n.id === l.target; })[0];
        var line = linesEl[li++];
        if (a && b && line) {
          line.setAttribute("x1", a.x); line.setAttribute("y1", a.y);
          line.setAttribute("x2", b.x); line.setAttribute("y2", b.y);
        }
      });
      svg.querySelectorAll(".label").forEach(function (text, i) {
        var n = nodes.filter(function (x) { return x.kind === "project" || x.kind === "profile"; })[i];
        if (n) { text.setAttribute("x", n.x + 9); text.setAttribute("y", n.y + 3); }
      });
      driftTimer = requestAnimationFrame(tick);
    }
    driftTimer = requestAnimationFrame(tick);
  }

  function loadGraph() {
    invoke("notch_graph").then(function (g) { if (mode === "graph" && g) draw(g); }).catch(function () { draw({ nodes: [], links: [] }); });
  }

  function onShow(payload) {
    payload = payload || {};
    renderLines(payload.lines || []);
    setMode(payload.mode || "graph");
  }

  if (!T) {
    document.documentElement.dataset.os = "macos";
    document.body.classList.add("preview");
    var alertPreview = location.search.indexOf("alert") >= 0;
    document.body.classList.add(alertPreview ? "preview-alert" : "preview-graph");
    onShow({
      mode: alertPreview ? "alert" : "graph",
      lines: ["Changed: projects/brain-project/historia.md", "Changed: projects/brain-project.md"]
    });
    if (!alertPreview) {
      draw({
        nodes: [
          { id: "p", label: "brain-project", kind: "project", path: "projects/brain-project.md" },
          { id: "h", label: "historia", kind: "file", path: "projects/brain-project/historia.md" },
          { id: "a", label: "arquitectura", kind: "file", path: "projects/brain-project/arquitectura.md" },
          { id: "m", label: "trabajo", kind: "memory", path: "memory/trabajo.md" },
          { id: "s", label: "organizar", kind: "skill", path: "skills/organizar.md" }
        ],
        links: [
          { source: "h", target: "p", kind: "part" },
          { source: "a", target: "p", kind: "part" },
          { source: "m", target: "p", kind: "related" },
          { source: "s", target: "p", kind: "related" }
        ]
      });
    }
    return;
  }

  T.event.listen("notch-show", function (e) { onShow(e.payload); });
  T.event.listen("notch-settled", function () {
    if (mode === "graph" && graph) draw(graph);
  });
  invoke("notch_state").then(function (s) {
    if (!s) return;
    document.documentElement.dataset.os = s.os || "";
    if (s.mode) onShow(s);
    else renderLines(s.lines || []);
  }).catch(function () {});
})();
