/* Componentes compartidos del dashboard y del chat (servido en /ui.js).
   - BrainUI.confirm(texto, {ok, cancel, danger, title}) → Promise<boolean>: ventana de confirmación
     propia en lugar del confirm() del navegador.
   - BrainUI.select(<select>): dropdown propio. El <select> nativo queda escondido como fuente de
     verdad (value, options, onchange siguen andando igual); si el código cambia .value o las opciones
     a mano, llamar BrainUI.sync(select). Las opciones nuevas y el atributo hidden se siguen solos.
   Usa las variables de color de cada página (--line, --text…). Textos de los botones: los pasa quien llama. */
(function () {
  const css = `
  .bui-dd { position: relative; display: inline-flex; min-width: 0; max-width: 100%; }
  .bui-dd.full { display: flex; width: 100%; }
  .bui-dd-btn { display: flex; align-items: center; gap: 8px; min-width: 0; width: 100%; cursor: pointer; text-align: left; }
  .bui-dd-btn .bui-dd-val { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .bui-dd-btn .bui-dd-chev { width: 10px; height: 6px; flex: none; color: var(--text-3); transition: transform .15s; }
  .bui-dd-btn[aria-expanded="true"] .bui-dd-chev { transform: rotate(180deg); }
  .bui-menu { position: fixed; z-index: 1000; min-width: 180px; max-height: min(320px, 60vh); overflow: auto; margin: 0; padding: 4px;
    list-style: none; background: var(--surface, var(--bg)); color: var(--text); border: 1px solid var(--line-strong);
    border-radius: var(--bui-radius, 12px); box-shadow: 0 1px 2px rgba(10,10,10,.06), 0 12px 32px -12px rgba(10,10,10,.28); }
  .bui-menu li { display: flex; align-items: center; gap: 8px; padding: 7px 10px; border-radius: calc(var(--bui-radius, 12px) - 4px);
    cursor: pointer; font-size: 13.5px; line-height: 1.3; }
  .bui-menu li .bui-tick { width: 14px; flex: none; display: grid; place-items: center; }
  .bui-menu li .bui-tick svg { width: 12px; height: 12px; }
  .bui-menu li .bui-sub { margin-left: auto; padding-left: 12px; font: 400 11.5px/1 "Courier Prime", monospace; color: var(--text-3); white-space: nowrap; }
  .bui-menu li.on { background: var(--surface-2, var(--soft)); }
  .bui-menu li[aria-selected="true"] { font-weight: 700; }
  .bui-menu li[aria-disabled="true"] { opacity: .45; cursor: default; }
  .bui-back { position: fixed; inset: 0; z-index: 1100; display: grid; place-items: center; padding: 16px;
    background: rgba(10,10,10,.38); animation: bui-in .12s ease-out; }
  .bui-dialog { width: min(420px, 100%); background: var(--surface, var(--bg)); color: var(--text); border: 1px solid var(--line-strong);
    border-radius: var(--bui-radius, 16px); padding: 20px 20px 16px; box-shadow: 0 24px 60px -20px rgba(0,0,0,.45); }
  .bui-dialog h2 { margin: 0 0 6px; font: 800 18px/1.2 "Archivo", sans-serif; letter-spacing: -.01em; }
  .bui-dialog p { margin: 0; color: var(--text-2); font-size: 14.5px; line-height: 1.5; white-space: pre-line; }
  .bui-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 18px; flex-wrap: wrap; }
  .bui-actions button { height: 36px; padding: 0 16px; border-radius: var(--bui-btn-radius, 999px); border: 1px solid var(--line-strong);
    background: var(--surface, var(--bg)); color: var(--text); font: 600 13.5px/1 "Archivo", sans-serif; cursor: pointer; }
  .bui-actions button:hover { background: var(--surface-2, var(--soft)); }
  .bui-actions .bui-ok { background: var(--text); color: var(--surface, var(--bg)); border-color: var(--text); }
  .bui-actions .bui-ok:hover { background: var(--text); opacity: .88; }
  .bui-actions .bui-ok.danger { background: var(--bad); border-color: var(--bad); color: #fff; }
  @keyframes bui-in { from { opacity: 0; } }
  @media (prefers-reduced-motion: reduce) { .bui-back { animation: none; } }`;
  const style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);

  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const CHEV = '<svg class="bui-dd-chev" viewBox="0 0 10 6" aria-hidden="true"><path d="M1 1l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>';
  const TICK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m5 12 5 5 9-10"/></svg>';

  // ---------- confirmación ----------
  function confirmBox(text, opt = {}) {
    return new Promise(resolve => {
      const prev = document.activeElement;
      const back = document.createElement("div");
      back.className = "bui-back";
      back.innerHTML = `<div class="bui-dialog" role="alertdialog" aria-modal="true" aria-labelledby="bui-t" aria-describedby="bui-p">
        ${opt.title ? `<h2 id="bui-t">${esc(opt.title)}</h2>` : ""}<p id="bui-p">${esc(text)}</p>
        <div class="bui-actions"><button type="button" class="bui-cancel">${esc(opt.cancel || "Cancel")}</button>
        <button type="button" class="bui-ok ${opt.danger ? "danger" : ""}">${esc(opt.ok || "OK")}</button></div></div>`;
      if (!opt.title) back.querySelector(".bui-dialog").setAttribute("aria-labelledby", "bui-p");
      document.body.appendChild(back);
      const ok = back.querySelector(".bui-ok"), cancel = back.querySelector(".bui-cancel");
      const done = v => {
        document.removeEventListener("keydown", key, true);
        back.remove();
        if (prev && prev.focus) prev.focus();
        resolve(v);
      };
      const key = e => {
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); done(false); }
        else if (e.key === "Tab") {  // el foco no sale de la ventana
          e.preventDefault();
          (document.activeElement === ok ? cancel : ok).focus();
        }
      };
      document.addEventListener("keydown", key, true);
      ok.onclick = () => done(true);
      cancel.onclick = () => done(false);
      back.addEventListener("mousedown", e => { if (e.target === back) done(false); });
      // lo destructivo arranca con el foco en Cancelar (un Enter apurado no borra nada)
      (opt.danger ? cancel : ok).focus();
    });
  }

  // ---------- dropdown ----------
  const all = new Map();  // select → {wrap, btn}
  let openMenu = null;

  function closeMenu(focusBtn) {
    if (!openMenu) return;
    const { menu, btn } = openMenu;
    menu.remove();
    btn.setAttribute("aria-expanded", "false");
    openMenu = null;
    if (focusBtn) btn.focus();
  }

  function label(opt) {
    return opt ? (opt.dataset.label || opt.textContent) : "";
  }

  function sync(sel) {
    const d = all.get(sel);
    if (!d) return;
    const opt = sel.options[sel.selectedIndex];
    d.btn.querySelector(".bui-dd-val").textContent = label(opt) || sel.dataset.placeholder || "";
    d.btn.disabled = sel.disabled;
    d.wrap.hidden = sel.hidden;
    d.btn.title = sel.title || "";
    const al = sel.getAttribute("aria-label");
    if (al) d.btn.setAttribute("aria-label", `${al}: ${label(opt)}`);
  }

  function open(sel) {
    const d = all.get(sel);
    closeMenu(false);
    const menu = document.createElement("ul");
    menu.className = "bui-menu";
    menu.setAttribute("role", "listbox");
    menu.id = "bui-menu";
    const opts = [...sel.options];
    menu.innerHTML = opts.map((o, i) => `<li role="option" id="bui-o${i}" data-i="${i}" aria-selected="${o.selected}" ${o.disabled ? 'aria-disabled="true"' : ""}>
      <span class="bui-tick">${o.selected ? TICK : ""}</span><span>${esc(label(o))}</span>${o.dataset.sub ? `<span class="bui-sub">${esc(o.dataset.sub)}</span>` : ""}</li>`).join("");
    document.body.appendChild(menu);
    // posición: debajo del botón, o arriba si no entra
    const r = d.btn.getBoundingClientRect();
    menu.style.minWidth = Math.max(180, r.width) + "px";
    const h = menu.offsetHeight, w = menu.offsetWidth;
    const below = window.innerHeight - r.bottom - 8 >= h || r.top < h + 8;
    menu.style.top = (below ? r.bottom + 4 : r.top - h - 4) + "px";
    menu.style.left = Math.max(8, Math.min(r.left, window.innerWidth - w - 8)) + "px";
    d.btn.setAttribute("aria-expanded", "true");
    d.btn.setAttribute("aria-controls", "bui-menu");
    let active = Math.max(0, sel.selectedIndex);
    const items = [...menu.children];
    const mark = i => {
      items.forEach(li => li.classList.remove("on"));
      if (!items[i]) return;
      active = i;
      items[i].classList.add("on");
      items[i].scrollIntoView({ block: "nearest" });
      d.btn.setAttribute("aria-activedescendant", items[i].id);
    };
    mark(active);
    const choose = i => {
      if (!opts[i] || opts[i].disabled) return;
      const changed = sel.selectedIndex !== i;
      sel.selectedIndex = i;
      sync(sel);
      closeMenu(true);
      if (changed) sel.dispatchEvent(new Event("change", { bubbles: true }));
    };
    menu.addEventListener("mousedown", e => e.preventDefault());  // el foco se queda en el botón
    menu.addEventListener("click", e => { const li = e.target.closest("li"); if (li) choose(+li.dataset.i); });
    menu.addEventListener("mousemove", e => { const li = e.target.closest("li"); if (li && +li.dataset.i !== active) mark(+li.dataset.i); });
    openMenu = { menu, btn: d.btn, sel, mark, choose, get active() { return active; }, count: items.length };
  }

  function enhance(sel) {
    if (all.has(sel)) return sel;
    const wrap = document.createElement("div");
    wrap.className = "bui-dd" + (sel.classList.contains("full") ? " full" : "");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = sel.className + " bui-dd-btn";
    btn.setAttribute("aria-haspopup", "listbox");
    btn.setAttribute("aria-expanded", "false");
    if (sel.getAttribute("style")) btn.setAttribute("style", sel.getAttribute("style"));
    btn.innerHTML = `<span class="bui-dd-val"></span>${CHEV}`;
    sel.parentNode.insertBefore(wrap, sel);
    wrap.appendChild(btn);
    wrap.appendChild(sel);
    sel.hidden = sel.hidden;  // conserva el estado; el wrap lo copia
    sel.style.display = "none";
    sel.tabIndex = -1;
    // un <label for="id-del-select"> tiene que llevar al botón
    if (sel.id) document.querySelectorAll(`label[for="${sel.id}"]`).forEach(l => l.addEventListener("click", e => { e.preventDefault(); btn.focus(); }));
    all.set(sel, { wrap, btn });
    btn.addEventListener("click", () => (openMenu && openMenu.sel === sel ? closeMenu(true) : open(sel)));
    btn.addEventListener("keydown", e => {
      const m = openMenu && openMenu.sel === sel ? openMenu : null;
      if (!m) {
        if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) { e.preventDefault(); open(sel); }
        return;
      }
      if (e.key === "ArrowDown") { e.preventDefault(); m.mark(Math.min(m.count - 1, m.active + 1)); }
      else if (e.key === "ArrowUp") { e.preventDefault(); m.mark(Math.max(0, m.active - 1)); }
      else if (e.key === "Home") { e.preventDefault(); m.mark(0); }
      else if (e.key === "End") { e.preventDefault(); m.mark(m.count - 1); }
      else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); m.choose(m.active); }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeMenu(true); }
      else if (e.key === "Tab") closeMenu(false);
    });
    new MutationObserver(() => sync(sel)).observe(sel, { childList: true, subtree: true, attributes: true, attributeFilter: ["hidden", "disabled", "title", "aria-label"] });
    sync(sel);
    return sel;
  }

  document.addEventListener("mousedown", e => {
    if (openMenu && !openMenu.menu.contains(e.target) && !openMenu.btn.contains(e.target)) closeMenu(false);
  });
  window.addEventListener("resize", () => closeMenu(false));
  document.addEventListener("scroll", e => { if (openMenu && !openMenu.menu.contains(e.target)) closeMenu(false); }, true);

  window.BrainUI = { confirm: confirmBox, select: enhance, sync, esc };
})();
