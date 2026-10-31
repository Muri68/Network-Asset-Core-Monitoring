/* NetCore UI helpers: theme, toasts, command palette data, charts. */

(function () {
  const root = document.documentElement;

  window.netcore = window.netcore || {};

  netcore.setTheme = function (theme) {
    root.dataset.theme = theme;
    try { localStorage.setItem("theme", theme); } catch (e) {}
    document.dispatchEvent(new CustomEvent("themechange"));
  };

  netcore.toggleTheme = function () {
    netcore.setTheme(root.dataset.theme === "dark" ? "light" : "dark");
  };

  const css = (name) => getComputedStyle(root).getPropertyValue(name).trim();

  /* ---------------- IPv4 subnet maths ---------------- */

  const toInt = (ip) => ip.split(".").reduce((acc, o) => (acc << 8) + Number(o), 0) >>> 0;
  const toIp = (n) => [24, 16, 8, 0].map((s) => (n >>> s) & 255).join(".");

  /* Returns null for invalid input. Mirrors Python's ipaddress for /31 and /32. */
  netcore.subnet = function (input) {
    const m = String(input || "").trim().match(/^(\d{1,3}(?:\.\d{1,3}){3})\/(\d{1,2})$/);
    if (!m) return null;
    const octets = m[1].split(".").map(Number);
    const len = Number(m[2]);
    if (octets.some((o) => o > 255) || len > 32) return null;
    const ip = toInt(m[1]);
    const mask = len === 0 ? 0 : (0xffffffff << (32 - len)) >>> 0;
    const network = (ip & mask) >>> 0;
    const broadcast = (network | (~mask >>> 0)) >>> 0;
    const total = 2 ** (32 - len);
    let first, last, usable;
    if (len === 32) { first = last = network; usable = 1; }
    else if (len === 31) { first = network; last = broadcast; usable = 2; }
    else { first = network + 1; last = broadcast - 1; usable = total - 2; }
    return {
      cidr: `${toIp(network)}/${len}`,
      hostBits: ip !== network,
      network: toIp(network),
      broadcast: len >= 31 ? "—" : toIp(broadcast),
      netmask: toIp(mask),
      wildcard: toIp(~mask >>> 0),
      first: toIp(first),
      last: toIp(last),
      gateway: toIp(first),
      total: total.toLocaleString(),
      usable: usable.toLocaleString(),
      prefixlen: len,
      kind: /^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(toIp(network)) ? "Private" : "Public",
      hosts: total <= 64 ? Array.from({ length: total }, (_, i) => toIp(network + i)) : [],
    };
  };

  /* ---------------- Charts ---------------- */

  const charts = new Map();

  function alpha(hex, a) {
    const n = parseInt(hex.replace("#", ""), 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
  }

  function baseOptions() {
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 500 },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: css("--surface-3"),
          borderColor: css("--line-strong"),
          borderWidth: 1,
          titleColor: css("--ink"),
          bodyColor: css("--ink-2"),
          padding: 10,
          cornerRadius: 8,
          displayColors: false,
        },
      },
      scales: {
        x: {
          grid: { display: false },
          border: { display: false },
          ticks: { color: css("--muted"), font: { size: 10 }, maxRotation: 0, autoSkipPadding: 12 },
        },
        y: {
          grid: { color: css("--line"), drawTicks: false },
          border: { display: false },
          ticks: { color: css("--muted"), font: { size: 10 }, padding: 8 },
        },
      },
    };
  }

  const builders = {
    line(canvas, data) {
      const up = css("--up");
      const ctx = canvas.getContext("2d");
      const grad = ctx.createLinearGradient(0, 0, 0, canvas.clientHeight || 220);
      grad.addColorStop(0, alpha(up, 0.28));
      grad.addColorStop(1, alpha(up, 0));
      const opts = baseOptions();
      opts.scales.y.ticks.callback = (v) => `${v} ms`;
      opts.interaction = { mode: "index", intersect: false };
      return {
        type: "line",
        data: {
          labels: data.labels,
          datasets: [{
            data: data.values,
            borderColor: up,
            backgroundColor: grad,
            fill: true,
            tension: 0.35,
            borderWidth: 2,
            pointRadius: 0,
            pointHoverRadius: 4,
            spanGaps: false,
          }],
        },
        options: opts,
      };
    },

    uptimeBars(canvas, data) {
      const colour = (v) => (v >= 99.5 ? css("--up") : v >= 98 ? css("--warn") : css("--down"));
      const opts = baseOptions();
      opts.scales.y.min = data.min || 95;
      opts.scales.y.max = 100;
      opts.scales.y.ticks.callback = (v) => `${v}%`;
      opts.scales.x.stacked = true;
      opts.plugins.tooltip.callbacks = { label: (c) => (c.datasetIndex === 1 ? `${c.raw}% uptime` : null) };
      opts.plugins.tooltip.filter = (c) => c.datasetIndex === 1;
      return {
        type: "bar",
        data: {
          labels: data.labels,
          datasets: [
            {
              data: data.values.map(() => 100),
              backgroundColor: css("--surface-3"),
              borderRadius: 6,
              borderSkipped: false,
              barPercentage: 0.55,
              grouped: false,
              order: 2,
            },
            {
              data: data.values,
              backgroundColor: data.values.map((v) => {
                const g = canvas.getContext("2d").createLinearGradient(0, 0, 0, canvas.clientHeight || 220);
                g.addColorStop(0, colour(v));
                g.addColorStop(1, alpha(colour(v), 0.35));
                return g;
              }),
              borderRadius: 6,
              borderSkipped: false,
              barPercentage: 0.55,
              grouped: false,
              order: 1,
            },
          ],
        },
        options: opts,
      };
    },

    donut(canvas, data) {
      const palette = data.colors.map((c) => css(c));
      return {
        type: "doughnut",
        data: {
          labels: data.labels,
          datasets: [{
            data: data.values,
            backgroundColor: palette,
            borderColor: css("--surface"),
            borderWidth: 3,
            borderRadius: 4,
            spacing: 2,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          cutout: "78%",
          plugins: { legend: { display: false }, tooltip: baseOptions().plugins.tooltip },
        },
      };
    },
  };

  netcore.renderCharts = function (scope) {
    if (!window.Chart) return;
    (scope || document).querySelectorAll("canvas[data-chart]").forEach((canvas) => {
      const source = document.getElementById(canvas.dataset.source);
      if (!source) return;
      const data = JSON.parse(source.textContent);
      if (charts.has(canvas)) charts.get(canvas).destroy();
      charts.set(canvas, new Chart(canvas, builders[canvas.dataset.chart](canvas, data)));
    });
  };

  document.addEventListener("DOMContentLoaded", () => netcore.renderCharts());
  document.addEventListener("themechange", () => netcore.renderCharts());
  document.addEventListener("htmx:afterSwap", (e) => netcore.renderCharts(e.target));

  /* ---------------- Alpine stores ---------------- */

  document.addEventListener("alpine:init", () => {
    Alpine.store("toast", {
      items: [],
      show(message, tone = "up") {
        const id = Date.now() + Math.random();
        this.items.push({ id, message, tone });
        setTimeout(() => (this.items = this.items.filter((t) => t.id !== id)), 3500);
      },
    });
  });

  /* Prototype forms: never submit, just confirm. */
  document.addEventListener("submit", (e) => {
    const form = e.target;
    if (!form.matches("[data-prototype]")) return;
    e.preventDefault();
    const msg = form.dataset.prototype || "Saved";
    window.Alpine && Alpine.store("toast").show(`${msg} — prototype only, nothing was stored`);
    form.dispatchEvent(new CustomEvent("prototype-submitted", { bubbles: true }));
  });

  /* HTMX: send the CSRF token on every request. */
  document.addEventListener("htmx:configRequest", (e) => {
    const token = document.querySelector('meta[name="csrf-token"]');
    if (token) e.detail.headers["X-CSRFToken"] = token.content;
  });
})();
