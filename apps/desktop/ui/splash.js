(function () {
  var es = (navigator.language || "").toLowerCase().indexOf("es") === 0;
  if (es) {
    document.documentElement.lang = "es";
    document.getElementById("msg").textContent = "Iniciando brain…";
    document.querySelectorAll("p")[1].textContent = "El primer arranque puede tardar un minuto mientras se levantan el dashboard y sus servicios.";
  }
})();
