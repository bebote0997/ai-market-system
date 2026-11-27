(function () {
  var root = document.querySelector(".lg");
  if (!root) return;
  var key = "aif-liquid-theme";
  var pageSize = 8;
  function systemMode() {
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  function applyTheme() {
    var saved = null;
    try { saved = localStorage.getItem(key); } catch (err) { saved = null; }
    var mode = saved === "light" || saved === "dark" ? saved : systemMode();
    root.setAttribute("data-theme", mode);
    var toggle = root.querySelector("[data-theme-toggle]");
    if (toggle) toggle.textContent = mode === "dark" ? "Modo claro" : "Modo oscuro";
  }
  function show(name) {
    root.querySelectorAll("[data-panel]").forEach(function (panel) {
      panel.hidden = panel.getAttribute("data-panel") !== name;
    });
    root.querySelectorAll("[data-nav]").forEach(function (item) {
      if (item.getAttribute("data-nav") === name) item.setAttribute("aria-current", "page");
      else item.removeAttribute("aria-current");
    });
    root.removeAttribute("data-open");
  }
  function rangeBounds(name) {
    var now = Number(root.getAttribute("data-now")) * 1000;
    if (name === "30") return [now - 30 * 86400000, now];
    if (name === "90") return [now - 90 * 86400000, now];
    if (name === "365") return [now - 365 * 86400000, now];
    if (name === "custom") {
      var from = Date.parse(root.querySelector("[data-from]").value);
      var to = Date.parse(root.querySelector("[data-to]").value);
      if (!from || !to) return [0, 0];
      return [from, to + 86400000];
    }
    return [0, now];
  }
  function paint(name) {
    var bounds = rangeBounds(name);
    var shown = [];
    root.querySelectorAll("[data-trade]").forEach(function (row) {
      var stamp = Number(row.getAttribute("data-epoch"));
      var keep = !stamp || (stamp * 1000 >= bounds[0] && bounds[1] > stamp * 1000);
      row.hidden = !keep;
      if (keep) shown.push(row);
    });
    var page = Number(root.getAttribute("data-page") || "0");
    var pages = Math.max(1, Math.ceil(shown.length / pageSize));
    if (page > pages - 1) page = 0;
    root.setAttribute("data-page", String(page));
    shown.forEach(function (row, index) {
      var on = index >= page * pageSize && page * pageSize + pageSize > index;
      row.hidden = !on;
    });
    var label = root.querySelector("[data-page-label]");
    if (label) label.textContent = "Página " + String(page + 1) + " de " + String(pages);
    root.querySelectorAll("[data-chart]").forEach(function (node) {
      node.hidden = node.getAttribute("data-chart") !== name;
    });
    root.querySelectorAll("[data-range]").forEach(function (button) {
      if (button.getAttribute("data-range") === name) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
  }
  var tabKey = "aif-liquid-section";
  var scrollKey = "aif-liquid-scroll";
  var mainKey = "aif-liquid-main-scroll";
  function remember() {
    var current = root.querySelector("[data-nav][aria-current='page']");
    var main = root.querySelector(".main");
    try {
      if (current) sessionStorage.setItem(tabKey, current.getAttribute("data-nav"));
      sessionStorage.setItem(scrollKey, String(window.scrollY || 0));
      if (main) sessionStorage.setItem(mainKey, String(main.scrollTop || 0));
    } catch (err) {}
  }
  function restore() {
    var name = null;
    var y = 0;
    var inner = 0;
    try {
      name = sessionStorage.getItem(tabKey);
      y = Number(sessionStorage.getItem(scrollKey) || "0");
      inner = Number(sessionStorage.getItem(mainKey) || "0");
    } catch (err) {}
    if (name === "Inicio" || name === "Posiciones" || name === "Historial" || name === "Riesgo" || name === "Sistema") show(name);
    var main = root.querySelector(".main");
    if (main && inner) main.scrollTop = inner;
    if (y) window.scrollTo(0, y);
  }
  applyTheme();
  var toggle = root.querySelector("[data-theme-toggle]");
  if (toggle) toggle.addEventListener("click", function () {
    var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
    try { localStorage.setItem(key, next); } catch (err) {}
    applyTheme();
  });
  root.querySelectorAll("[data-nav]").forEach(function (button) {
    button.addEventListener("click", function () {
      show(button.getAttribute("data-nav"));
      remember();
    });
  });
  window.addEventListener("scroll", remember, {passive: true});
  var scroller = root.querySelector(".main");
  if (scroller) scroller.addEventListener("scroll", remember, {passive: true});
  var burger = root.querySelector("[data-menu]");
  if (burger) burger.addEventListener("click", function () {
    if (root.getAttribute("data-open") === "1") root.removeAttribute("data-open");
    else root.setAttribute("data-open", "1");
  });
  root.querySelectorAll("[data-range]").forEach(function (button) {
    button.addEventListener("click", function () {
      root.setAttribute("data-page", "0");
      paint(button.getAttribute("data-range"));
    });
  });
  root.querySelectorAll("[data-config]").forEach(function (button) {
    button.addEventListener("click", function () {
      var name = button.getAttribute("data-config");
      root.querySelectorAll("[data-config-panel]").forEach(function (panel) {
        panel.hidden = panel.getAttribute("data-config-panel") !== name;
      });
      root.querySelectorAll("[data-config]").forEach(function (item) {
        if (item.getAttribute("data-config") === name) item.setAttribute("aria-current", "page");
        else item.removeAttribute("aria-current");
      });
    });
  });
  var prev = root.querySelector("[data-prev]");
  var next = root.querySelector("[data-next]");
  if (prev) prev.addEventListener("click", function () {
    var page = Number(root.getAttribute("data-page") || "0");
    if (page > 0) root.setAttribute("data-page", String(page - 1));
    paint(currentRange());
  });
  if (next) next.addEventListener("click", function () {
    var page = Number(root.getAttribute("data-page") || "0");
    root.setAttribute("data-page", String(page + 1));
    paint(currentRange());
  });
  function currentRange() {
    var current = root.querySelector("[data-range][aria-current='page']");
    return current ? current.getAttribute("data-range") : "30";
  }
  var exporter = root.querySelector("[data-export]");
  if (exporter) exporter.addEventListener("click", function () {
    var lines = ["Fecha,Par,Lado,Lotes,Entrada,Salida,Motivo,Resultado"];
    root.querySelectorAll("[data-trade]").forEach(function (row) {
      if (row.hidden) return;
      lines.push(["fecha", "par", "lado", "lotes", "entrada", "salida", "motivo", "resultado"].map(function (name) {
        return '"' + String(row.getAttribute("data-" + name) || "").replace(/"/g, "''") + '"';
      }).join(","));
    });
    var blob = new Blob([lines.join("\n")], {type: "text/csv"});
    var link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "historial.csv";
    link.click();
  });
  ["data-from", "data-to"].forEach(function (name) {
    var field = root.querySelector("[" + name + "]");
    if (!field) return;
    field.addEventListener("change", function () {
      root.setAttribute("data-page", "0");
      paint("custom");
    });
  });
  paint("30");
  restore();
})();
