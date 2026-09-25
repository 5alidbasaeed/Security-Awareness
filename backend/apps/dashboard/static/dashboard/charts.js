/*
  Risk trend charts. Data arrives server-rendered in a <script type="application/json">
  block (no fetch, no inline script — the page CSP forbids it). Colors are read
  from the CSS tokens, and the thresholds come from the backend's own constants,
  so the chart, the badges and the scoring algorithm can never drift apart.
*/
(function () {
  "use strict";

  var charts = [];

  function token(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function reduceMotion() {
    return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  // Rates per campaign / response speed: several labelled series on a 0-100% axis. Series alternate
  // solid and dashed so the chart still reads without colour.
  function drawLines(canvas, data, grid, muted) {
    var unit = data.unit || "";
    var bar = data.style === "bar";
    var surface = token("--surface");
    charts.push(new Chart(canvas, {
      type: bar ? "bar" : "line",
      data: {
        labels: data.labels,
        datasets: data.datasets.map(function (series) {
          var color = token(series.color);
          if (bar) {
            // The second series is an outlined bar, so the pair still reads in greyscale.
            return {
              label: series.label,
              data: series.values,
              backgroundColor: series.dashed ? surface : color,
              borderColor: color,
              borderWidth: series.dashed ? 2 : 0,
              borderRadius: 3,
              maxBarThickness: 28,
              categoryPercentage: 0.6,
              barPercentage: 0.9,
            };
          }
          return {
            label: series.label,
            data: series.values,
            borderColor: color,
            backgroundColor: color,
            borderWidth: 2,
            borderDash: series.dashed ? [6, 4] : [],
            pointStyle: series.dashed ? "triangle" : "circle",
            pointRadius: 3,
            pointHoverRadius: 5,
            // Cumulative shares only change at a sample, so they step; never a curve between samples.
            stepped: data.style === "step" ? "after" : false,
            spanGaps: true,
          };
        }),
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: reduceMotion() ? false : { duration: 400 },
        animations: { x: { duration: 0 } },
        interaction: { mode: "index", intersect: false },
        scales: {
          y: { min: 0, max: data.y_max, ticks: { color: muted, stepSize: 25, callback: function (v) { return v + unit; } }, grid: { color: grid }, border: { display: false } },
          x: { ticks: { color: muted, maxTicksLimit: 8, maxRotation: 0 }, grid: { display: false }, border: { color: grid } },
        },
        plugins: {
          legend: { position: "bottom", labels: { color: muted, usePointStyle: !bar, boxWidth: bar ? 12 : 8, boxHeight: 8 } },
          tooltip: { callbacks: { label: function (item) { return item.dataset.label + ": " + (item.parsed.y === null ? "no data" : item.parsed.y + unit); } } },
        },
      },
    }));
  }

  function draw(canvas) {
    var source = document.getElementById(canvas.dataset.chartSource);
    if (!source || typeof Chart === "undefined") return;
    var data = JSON.parse(source.textContent);
    if (!data.has_data) return;

    var accent = token("--accent");
    var grid = token("--border");
    var muted = token("--text-secondary");
    if (data.kind === "lines") { drawLines(canvas, data, grid, muted); return; }
    var threshold = function (value, color, label) {
      return {
        label: label,
        data: data.labels.map(function () { return value; }),
        borderColor: color,
        borderWidth: 1,
        borderDash: [4, 4],
        pointRadius: 0,
        pointStyle: "line",
        fill: false,
      };
    };

    // Scale the axis to the data, keeping the medium threshold (and the high one once anyone is
    // near it) in view: a fixed 0-100 axis flattens a typical 0-25 range against the floor.
    var peak = Math.max.apply(null, data.values.filter(function (v) { return v !== null; }).concat([0]));
    var ceiling = peak >= data.medium ? data.high : data.medium;
    var yMax = Math.min(100, Math.ceil(Math.max(peak, ceiling) / 10) * 10 + 10);

    var chart = new Chart(canvas, {
      type: "line",
      data: {
        labels: data.labels,
        datasets: [
          {
            label: data.title,
            data: data.values,
            borderColor: accent,
            backgroundColor: accent,
            borderWidth: 2,
            // Straight segments: scores are snapshots, and any curve between them shows values no
            // snapshot ever had.
            tension: 0,
            pointRadius: data.values.length > 30 ? 0 : 3,
            pointHoverRadius: 5,
            spanGaps: true,
          },
          threshold(data.medium, token("--risk-medium"), "Medium risk from " + data.medium),
          threshold(data.high, token("--risk-high"), "High risk from " + data.high),
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        // Calm entrance: the line rises into place; no sideways slide. None at all if the OS asks for less motion.
        animation: reduceMotion() ? false : { duration: 400 },
        animations: { x: { duration: 0 } },
        interaction: { mode: "index", intersect: false },
        scales: {
          y: { min: 0, max: yMax, ticks: { color: muted, maxTicksLimit: 6 }, grid: { color: grid }, border: { display: false } },
          x: { ticks: { color: muted, maxTicksLimit: 8, maxRotation: 0 }, grid: { display: false }, border: { color: grid } },
        },
        plugins: {
          legend: { position: "bottom", labels: { color: muted, usePointStyle: true, boxWidth: 8, filter: function (item) { return item.datasetIndex !== 0; } } },
          tooltip: { filter: function (item) { return item.datasetIndex === 0; } },
        },
      },
    });
    charts.push(chart);
  }

  function drawAll() {
    charts.forEach(function (chart) { chart.destroy(); });
    charts = [];
    document.querySelectorAll("canvas[data-chart-source]").forEach(draw);
  }

  document.addEventListener("DOMContentLoaded", drawAll);
  // Follow the OS light/dark switch: token colors are re-read on redraw.
  if (window.matchMedia) {
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", drawAll);
  }
})();
