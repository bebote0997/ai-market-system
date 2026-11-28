(function () {
  "use strict";
  var POLL_MS = 5000;

  function themeColors() {
    var root = document.querySelector(".lg");
    var fallback = { ink: "#14324a", pos: "#0f7a45", neg: "#c81e3a", stroke: "rgba(19,41,75,.12)", accent: "#2563eb" };
    if (!root) return fallback;
    var style = getComputedStyle(root);
    function read(name, backup) {
      return (style.getPropertyValue(name) || backup).trim();
    }
    return {
      ink: read("--lg-ink", fallback.ink),
      pos: read("--lg-pos", fallback.pos),
      neg: read("--lg-neg", fallback.neg),
      stroke: read("--lg-stroke", fallback.stroke),
      accent: read("--lg-chart", fallback.accent)
    };
  }

  function toTime(value) {
    if (value == null) return null;
    if (typeof value === "number" && isFinite(value)) {
      return value > 1e12 ? Math.floor(value / 1000) : Math.floor(value);
    }
    var ms = Date.parse(value);
    return isFinite(ms) ? Math.floor(ms / 1000) : null;
  }

  function toNum(value) {
    if (value == null || value === "") return null;
    var n = Number(value);
    return isFinite(n) ? n : null;
  }

  function clock(iso) {
    if (!iso) return "--:--";
    var date = new Date(iso);
    if (isNaN(date.getTime())) return "--:--";
    var parts = new Intl.DateTimeFormat("en-GB", {
      timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false
    }).formatToParts(date);
    var map = {};
    parts.forEach(function (part) { map[part.type] = part.value; });
    return (map.hour || "--") + ":" + (map.minute || "--");
  }

  function bars(rows) {
    var out = [];
    (rows || []).forEach(function (row) {
      var time = toTime(row && row.time);
      var open = toNum(row && row.open);
      var high = toNum(row && row.high);
      var low = toNum(row && row.low);
      var close = toNum(row && row.close);
      if (time == null || open == null || high == null || low == null || close == null) return;
      out.push({ time: time, open: open, high: high, low: low, close: close });
    });
    return out;
  }

  function money(value) {
    var n = toNum(value);
    if (n == null) return "0.00";
    return (n < 0 ? "−" : "") + Math.abs(n).toFixed(2);
  }

  function lastClock(statusEl) {
    var text = statusEl ? statusEl.textContent : "";
    var found = text && text.match(/(\d{2}:\d{2})$/);
    return found ? found[1] : "--:--";
  }

  function bind() {
    var el = document.querySelector(".lg .live-chart");
    var api = window.LightweightCharts;
    if (!el || !api || !api.createChart) return false;
    if (window.__aifChartTimer) {
      clearInterval(window.__aifChartTimer);
      window.__aifChartTimer = null;
    }
    if (window.__aifChart) {
      try { window.__aifChart.remove(); } catch (err) {}
      window.__aifChart = null;
    }

    var card = el.closest(".live-chart-card") || el.parentElement;
    var statusEl = card ? card.querySelector("[data-chart-status]") : null;
    var posEl = card ? card.querySelector("[data-chart-position]") : null;
    var symbol = (el.getAttribute("data-symbol") || "USDJPY").toUpperCase();
    var colors = themeColors();
    var chart = api.createChart(el, {
      autoSize: true,
      layout: {
        background: { type: "solid", color: "transparent" },
        textColor: colors.ink,
        fontFamily: "Segoe UI,system-ui,sans-serif"
      },
      grid: { vertLines: { color: colors.stroke }, horzLines: { color: colors.stroke } },
      rightPriceScale: { borderColor: colors.stroke },
      timeScale: { borderColor: colors.stroke, timeVisible: true, secondsVisible: false },
      crosshair: { mode: 0 },
      handleScroll: { pressedMouseMove: true, mouseWheel: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: false }
    });
    var precision = symbol.indexOf("JPY") >= 0 ? 3 : 5;
    var series = chart.addSeries(api.CandlestickSeries, {
      upColor: colors.pos,
      downColor: colors.neg,
      borderUpColor: colors.pos,
      borderDownColor: colors.neg,
      wickUpColor: colors.pos,
      wickDownColor: colors.neg,
      priceFormat: { type: "price", precision: precision, minMove: Math.pow(10, -precision) }
    });
    var lines = [];
    var lastBars = [];
    window.__aifChart = chart;

    function clearLines() {
      lines.forEach(function (line) {
        try { series.removePriceLine(line); } catch (err) {}
      });
      lines = [];
    }

    function setStatus(text) {
      if (statusEl) statusEl.textContent = text;
    }

    function setPosition(position) {
      if (!posEl) return;
      if (!position) {
        posEl.textContent = "Sin posición · 0.00 USC";
        return;
      }
      var side = position.side === "LONG" ? "Compra" : position.side === "SHORT" ? "Venta" : "Posición";
      posEl.textContent = side + " · " + money(position.floating) + " USC";
    }

    function drawLines(position) {
      clearLines();
      if (!position) {
        series.applyOptions({ autoscaleInfoProvider: undefined });
        return;
      }
      var entry = toNum(position.entry);
      var stop = toNum(position.stop);
      var target = toNum(position.target);
      var specs = [];
      if (entry != null) specs.push({ price: entry, color: colors.accent, lineStyle: 0, title: "Entrada " + entry });
      if (stop != null) specs.push({ price: stop, color: colors.neg, lineStyle: 2, title: "SL " + stop });
      if (target != null) specs.push({ price: target, color: colors.pos, lineStyle: 2, title: "TP " + target });
      var ext = specs.map(function (spec) { return spec.price; });
      series.applyOptions({
        autoscaleInfoProvider: function (orig) {
          var range = orig();
          if (!range || !ext.length) return range;
          var lo = Math.min.apply(null, ext.concat([range.priceRange.minValue]));
          var hi = Math.max.apply(null, ext.concat([range.priceRange.maxValue]));
          var pad = (hi - lo) * 0.05;
          return { priceRange: { minValue: lo - pad, maxValue: hi + pad }, margins: range.margins };
        }
      });
      specs.forEach(function (spec) {
        lines.push(series.createPriceLine({
          price: spec.price,
          color: spec.color,
          lineWidth: 2,
          lineStyle: spec.lineStyle,
          axisLabelVisible: true,
          title: spec.title
        }));
      });
    }

    function paint(data) {
      if (!data) return;
      var next = bars(data.candles);
      if (next.length) {
        series.setData(next);
        lastBars = next;
        var vis = next.slice(-130);
        try {
          chart.timeScale().setVisibleRange({ from: vis[0].time, to: next[next.length - 1].time + 300 });
        } catch (err) {}
      }
      drawLines(data.position || null);
      setPosition(data.position || null);
      var hhmm = clock(data.last_update);
      if (data.stale || !data.last_update) {
        setStatus("sin datos · última lectura " + hhmm);
      } else {
        var bid = data.bid != null && data.bid !== "" ? "Bid " + data.bid + " · " : "";
        setStatus(bid + "última lectura " + hhmm);
      }
    }

    function select(next) {
      symbol = next;
      el.setAttribute("data-symbol", next);
      if (card) {
        card.querySelectorAll("[data-chart-symbol]").forEach(function (btn) {
          if (btn.getAttribute("data-chart-symbol") === next) btn.setAttribute("aria-current", "page");
          else btn.removeAttribute("aria-current");
        });
      }
      precision = symbol.indexOf("JPY") >= 0 ? 3 : 5;
      series.applyOptions({
        priceFormat: { type: "price", precision: precision, minMove: Math.pow(10, -precision) }
      });
    }

    if (card && !card.dataset.chartClicks) {
      card.dataset.chartClicks = "1";
      card.addEventListener("click", function (event) {
        var btn = event.target.closest("[data-chart-symbol]");
        if (!btn) return;
        event.preventDefault();
        var next = (btn.getAttribute("data-chart-symbol") || "").toUpperCase();
        if (next !== "USDJPY" && next !== "EURUSD") return;
        if (next === symbol) return;
        select(next);
        tick();
      });
    }

    function tick() {
      fetch("/chart/" + encodeURIComponent(symbol) + ".json", {
        method: "GET",
        credentials: "same-origin",
        cache: "no-store"
      }).then(function (res) {
        if (!res.ok) throw new Error("chart");
        return res.json();
      }).then(paint).catch(function () {
        setStatus("sin datos · última lectura " + lastClock(statusEl));
      });
    }

    tick();
    window.__aifChartTimer = setInterval(tick, POLL_MS);

    if (!window.__aifChartThemeWatch && typeof MutationObserver === "function") {
      window.__aifChartThemeWatch = 1;
      var root = document.querySelector(".lg");
      if (root) {
        new MutationObserver(function () {
          if (!window.__aifChart) return;
          var next = themeColors();
          window.__aifChart.applyOptions({ layout: { textColor: next.ink } });
        }).observe(root, { attributes: true, attributeFilter: ["data-theme"] });
      }
    }
    return true;
  }

  function start() {
    if (bind()) return;
    if (!document.querySelector(".lg .live-chart")) return;
    window.__aifChartWait = (window.__aifChartWait || 0) + 1;
    if (window.__aifChartWait > 40) return;
    setTimeout(start, 50);
  }

  window.__aifChartWait = 0;
  start();
})();
