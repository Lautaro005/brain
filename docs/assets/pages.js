// Docs y Changelog: idioma (mismo localStorage que la landing), páginas por hash, índice lateral,
// "en esta página", anterior/siguiente, filtro del índice y botones de copiar. Sin dependencias.
(() => {
  const UI = {
    en: { onThis: "On this page", prev: "Previous", next: "Next", search: "Filter pages…  ( / )", none: "No pages match.", copy: "Copy", copied: "Copied", menu: "Menu", close: "Close" },
    es: { onThis: "En esta página", prev: "Anterior", next: "Siguiente", search: "Filtrar páginas…  ( / )", none: "Ninguna página coincide.", copy: "Copiar", copied: "Copiado", menu: "Índice", close: "Cerrar" },
  };
  const titles = window.PAGE_TITLES || { en: document.title, es: document.title };
  let lang = "en";
  try { lang = localStorage.getItem("brain-site-lang") || ((navigator.language || "").toLowerCase().startsWith("es") ? "es" : "en"); } catch (e) {}

  const pages = [...document.querySelectorAll("section.page")];
  // el hash (#memory) nombra una página, no un ancla: si coincidiera con el id de la <section>, el
  // navegador bajaría hasta ella al cargar. El script corre antes de que termine el parseo.
  pages.forEach(p => { p.dataset.page = p.id; p.id = `page-${p.id}`; });
  const side = document.getElementById("side-nav");
  const toc = document.getElementById("toc");
  const pager = document.getElementById("pager");
  const crumbs = document.getElementById("crumbs");
  const search = document.getElementById("side-search");
  const T = () => UI[lang];
  const pageTitle = p => p.dataset[lang === "es" ? "titleEs" : "titleEn"];
  const groupTitle = p => p.dataset[lang === "es" ? "groupEs" : "groupEn"];
  let current = null;

  function buildSide() {
    if (!side) return;
    const q = (search && search.value || "").trim().toLowerCase();
    const groups = [];
    pages.forEach((p, i) => {
      const hay = (pageTitle(p) + " " + p.querySelector(`.l-${lang}`)?.textContent).toLowerCase();
      if (q && !hay.includes(q)) return;
      let g = groups.find(g => g.name === groupTitle(p));
      if (!g) groups.push(g = { name: groupTitle(p), items: [] });
      g.items.push({ p, i });
    });
    side.innerHTML = groups.length ? groups.map(g => `<div class="side-group"><h4>${g.name}</h4>${g.items.map(({ p, i }) =>
      `<a href="#${p.dataset.page}"${p === current ? ' aria-current="page"' : ""}><span class="no">${i + 1}</span><span>${pageTitle(p)}</span></a>`).join("")}</div>`).join("")
      : `<p class="side-empty">${T().none}</p>`;
  }

  function slug(s) { return s.toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, ""); }

  function buildToc() {
    if (!toc || !current) return;
    const block = current.querySelector(`.l-${lang}`) || current;
    const hs = [...block.querySelectorAll("h2")];
    hs.forEach(h => { if (!h.id) h.id = `${current.id}--${slug(h.textContent)}`; });
    toc.innerHTML = hs.length ? `<h4>${T().onThis}</h4>` + hs.map(h => `<a href="#${current.dataset.page}" data-to="${h.id}">${h.textContent}</a>`).join("") : "";
    toc.querySelectorAll("a").forEach(a => a.onclick = e => { e.preventDefault(); document.getElementById(a.dataset.to).scrollIntoView(); });
    spy();
  }

  function spy() {
    if (!toc || !current) return;
    const links = [...toc.querySelectorAll("a")];
    let on = links[0];
    for (const a of links) { const h = document.getElementById(a.dataset.to); if (h && h.getBoundingClientRect().top < 140) on = a; }
    links.forEach(a => a.classList.toggle("on", a === on));
  }

  function buildPager() {
    if (!pager || !current) return;
    const i = pages.indexOf(current), prev = pages[i - 1], next = pages[i + 1];
    pager.innerHTML = (prev ? `<a class="card prev" href="#${prev.dataset.page}"><small>← ${T().prev}</small><b>${pageTitle(prev)}</b></a>` : "")
      + (next ? `<a class="card next" href="#${next.dataset.page}"><small>${T().next} →</small><b>${pageTitle(next)}</b></a>` : "");
    if (crumbs) crumbs.innerHTML = `<span>docs</span><span>${groupTitle(current)}</span><span>${pageTitle(current)}</span>`;
  }

  function show(id, scroll) {
    if (!pages.length) return;
    const p = pages.find(p => p.dataset.page === id) || pages[0];
    if (p !== current) {
      pages.forEach(x => x.classList.toggle("on", x === p));
      current = p;
      if (scroll) window.scrollTo(0, 0);
    }
    document.title = `${pageTitle(p)} · ${titles[lang]}`;
    document.body.classList.remove("menu-open");
    buildSide(); buildToc(); buildPager();
  }

  function setLang(l) {
    lang = UI[l] ? l : "en";
    document.documentElement.lang = lang;
    if (!pages.length) document.title = titles[lang];
    document.querySelectorAll(".lang button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.lang === lang)));
    document.querySelectorAll("[data-ui]").forEach(el => { el.textContent = T()[el.dataset.ui]; });
    if (search) search.placeholder = T().search;
    try { localStorage.setItem("brain-site-lang", lang); } catch (e) {}
    if (pages.length) show(current ? current.dataset.page : location.hash.slice(1), false);
    addCopy();
  }

  function addCopy() {
    document.querySelectorAll(".content pre").forEach(pre => {
      let b = pre.querySelector(".copy");
      if (!b) {
        b = document.createElement("button"); b.type = "button"; b.className = "copy";
        b.onclick = async () => {
          const text = [...pre.childNodes].filter(n => n !== b).map(n => n.textContent).join("").trim();
          try { await navigator.clipboard.writeText(text); } catch (e) {}
          b.classList.add("done"); b.textContent = T().copied;
          setTimeout(() => { b.classList.remove("done"); b.textContent = T().copy; }, 1600);
        };
        pre.appendChild(b);
      }
      b.textContent = T().copy;
    });
  }

  document.querySelectorAll(".lang button").forEach(b => b.addEventListener("click", () => setLang(b.dataset.lang)));
  window.addEventListener("hashchange", () => show(location.hash.slice(1), true));
  window.addEventListener("scroll", spy, { passive: true });
  if (search) {
    search.addEventListener("input", buildSide);
    document.addEventListener("keydown", e => {
      if (e.key === "/" && document.activeElement !== search && !/input|textarea/i.test(document.activeElement.tagName)) { e.preventDefault(); search.focus(); }
    });
  }
  const menu = document.getElementById("menu-btn");
  if (menu) menu.addEventListener("click", () => document.body.classList.toggle("menu-open"));
  setLang(lang);
})();
