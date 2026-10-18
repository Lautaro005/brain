(function () {
  var es = (navigator.language || "").toLowerCase().indexOf("es") === 0;
  var msg = decodeURIComponent((location.hash || "").slice(1));
  document.getElementById("msg").textContent = msg;
  if (es) {
    document.documentElement.lang = "es";
    document.getElementById("title").textContent = "brain no pudo arrancar";
    document.getElementById("how").textContent = "Si brain todavía no está instalado, corré esto en una terminal:";
    document.getElementById("logs").innerHTML = "El detalle está en <code>~/.brain/data/logs/app.log</code>.";
    document.getElementById("retry").textContent = "Reintentar";
  }
  document.getElementById("retry").onclick = function () {
    try { window.__TAURI__.core.invoke("retry_start"); } catch (e) { location.reload(); }
  };
})();
