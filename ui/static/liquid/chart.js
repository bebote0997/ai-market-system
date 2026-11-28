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

  function toDate(value) {
    if (value == null || value === "") return null;
    if (value instanceof Date) return isNaN(value.getTime()) ? null : value;
    if (typeof value === "number" && isFinite(value)) {
      return new Date(value > 1e12 ? value : value * 1000);
    }
    var ms = Date.parse(value);
    return isFinite(ms) ? new Date(ms) : null;
  }

  function formatEt(value) {
    var date = toDate(value);
    if (!date) return "";
    var parts = new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York",
      hour: "numeric",
      minute: "2-digit",
      hour12: true
    }).formatToParts(date);
    var map = {};
    parts.forEach(function (part) { map[part.type] = part.value; });
    if (!map.hour || !map.minute) return "";
    return map.hour + ":" + map.minute + " " + (map.dayPeriod || "").replace(/\./g, "").toUpperCase() + " ET";
  }

  function formatEtTick(time, tickMarkType) {
    var date = toDate(time);
    if (!date) return "";
    var type = tickMarkType;
    var kinds = window.LightweightCharts && window.LightweightCharts.TickMarkType;
    var year = kinds ? kinds.Year : 0;
    var month = kinds ? kinds.Month : 1;
    var day = kinds ? kinds.DayOfMonth : 2;
    if (type === year || type === 0) {
      return new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", year: "numeric" }).format(date);
    }
    if (type === month || type === 1) {
      return new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", month: "short", year: "numeric" }).format(date);
    }
    if (type === day || type === 2) {
      return new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", month: "short", day: "numeric" }).format(date);
    }
    var text = formatEt(date);
    return text.replace(" ET", "");
  }

  function nyWeekdayHour(date) {
    var parts = new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York",
      weekday: "short",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23"
    }).formatToParts(date);
    var map = {};
    parts.forEach(function (part) { map[part.type] = part.value; });
    return { weekday: map.weekday, minutes: (Number(map.hour) * 60) + Number(map.minute) };
  }

  function fxMarketClosed(now) {
    var date = toDate(now) || new Date();
    var slot = nyWeekdayHour(date);
    if (slot.weekday === "Sat") return true;
    if (slot.weekday === "Fri") return slot.minutes >= 17 * 60;
    if (slot.weekday === "Sun") return slot.minutes < 17 * 60;
    return false;
  }

  function chartStatus(data, now) {
    var next = bars(data && data.candles);
    if (!next.length) return "sin datos";
    var last = formatEt(next[next.length - 1].time);
    if (fxMarketClosed(now)) {
      return last ? "mercado cerrado · última vela " + last : "mercado cerrado";
    }
    if (data.stale) {
      return last ? "sin conexión · última vela " + last : "sin conexión";
    }
    var read = formatEt(data.last_update) || last;
    var bid = data.bid != null && data.bid !== "" ? "Bid " + data.bid + " · " : "";
    return bid + (read ? "última lectura " + read : "última lectura");
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
      timeScale: {
        borderColor: colors.stroke,
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: function (time, tickMarkType) { return formatEtTick(time, tickMarkType); }
      },
      localization: {
        timeFormatter: function (time) { return formatEt(time); }
      },
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

    function scaleMargins() {
      var narrow = (el.clientWidth || window.innerWidth || 1280) <= 414;
      return narrow ? { top: 0.14, bottom: 0.22 } : { top: 0.10, bottom: 0.12 };
    }
    chart.priceScale("right").applyOptions({ scaleMargins: scaleMargins() });

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
      var margins = scaleMargins();
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
      chart.priceScale("right").applyOptions({ scaleMargins: margins });
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
      setStatus(chartStatus(data, new Date()));
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
        setStatus(chartStatus({ candles: lastBars, stale: true, last_update: null }, new Date()));
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
