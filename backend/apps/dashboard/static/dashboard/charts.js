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

  function draw(canvas) {
    var source = document.getElementById(canvas.dataset.chartSource);
    if (!source || typeof Chart === "undefined") return;
    var data = JSON.parse(source.textContent);
    if (!data.has_data) return;

    var accent = token("--accent");
    var grid = token("--border");
    var muted = token("--text-secondary");
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
            tension: 0.3,
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
          y: { min: 0, max: 100, ticks: { color: muted, stepSize: 25 }, grid: { color: grid }, border: { display: false } },
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
