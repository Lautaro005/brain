(function () {
  var T = window.__TAURI__;
  var es = (navigator.language || "").toLowerCase().indexOf("es") === 0;
  var tx = es
    ? { sub: "cambios en tu vault", none: "Sin cambios recientes", panel: "Panel", grafo: "Grafo", chat: "Chat", ajustes: "Ajustes", close: "Cerrar" }
    : { sub: "changes in your vault", none: "No recent changes", panel: "Dashboard", grafo: "Graph", chat: "Chat", ajustes: "Settings", close: "Close" };
  document.documentElement.lang = es ? "es" : "en";
  document.getElementById("sub").textContent = tx.sub;
  document.getElementById("close").setAttribute("aria-label", tx.close);
  ["panel", "grafo", "chat", "ajustes"].forEach(function (k) { document.getElementById("b-" + k).textContent = tx[k]; });

  var timer = null, hover = false;
  function render(lines) {
    var ul = document.getElementById("list");
    ul.textContent = "";
    (lines && lines.length ? lines : [null]).forEach(function (l) {
      var li = document.createElement("li");
      li.textContent = l === null ? tx.none : l;
      if (l === null) li.className = "empty";
      ul.appendChild(li);
    });
    arm();
  }
  function hide() { try { T.core.invoke("hide_notch"); } catch (e) {} }
  function arm() {
    clearTimeout(timer);
    timer = setTimeout(function () { if (hover) arm(); else hide(); }, 9000);
  }
  document.body.addEventListener("mouseenter", function () { hover = true; });
  document.body.addEventListener("mouseleave", function () { hover = false; arm(); });
  document.getElementById("close").onclick = hide;
  document.querySelectorAll("nav button").forEach(function (b) {
    b.onclick = function () { T.core.invoke("open_view", { view: b.dataset.view }); hide(); };
  });
  addEventListener("keydown", function (e) { if (e.key === "Escape") hide(); });
  T.event.listen("notch-update", function (e) { render(e.payload); });
  T.core.invoke("notch_lines").then(render).catch(function () { render([]); });
})();
