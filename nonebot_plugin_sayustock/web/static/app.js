(() => {
  "use strict";

  const API = new URL("api", document.baseURI).pathname.replace(/\/$/, "");
  const MOUNT = new URL(".", document.baseURI).pathname.replace(/\/$/, "");
  const state = {
    view: "market",
    lastQuote: null,
  };

  const $ = (id) => document.getElementById(id);
  const esc = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[ch]));

  async function csrfToken() {
    const response = await fetch("/qweb/api/v1/auth/session", { credentials: "same-origin", cache: "no-store" });
    if (!response.ok) throw new Error("无法读取登录会话");
    const session = await response.json();
    if (!session.csrf_token) throw new Error("请登录七七");
    return session.csrf_token;
  }

  function toast(message, error = false) {
    const node = $("toast");
    node.textContent = message;
    node.className = "toast show" + (error ? " error" : "");
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { node.className = "toast"; }, 2600);
  }

  async function api(path, options = {}) {
    const method = String(options.method || "GET").toUpperCase();
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    if (method !== "GET" && method !== "HEAD") {
      const token = await csrfToken();
      if (token) headers["X-CSRF-Token"] = token;
    }
    const response = await fetch(API + path, {
      ...options,
      method,
      headers,
      credentials: "same-origin",
    });
    let payload = null;
    try { payload = await response.json(); } catch (_) {}
    if (!response.ok) {
      if (response.status === 401) toast("QWeb 宿主未识别当前成员身份", true);
      throw new Error((payload && (payload.detail || payload.message)) || `HTTP ${response.status}`);
    }
    return payload;
  }

  const titles = {
    market: ["市场总览", "指数、市场宽度与板块强弱"],
    stock: ["个股行情", "行情快照、K线与技术分析"],
    artifacts: ["图表与报告", "已生成的 HTML 制品"],
  };

  function showView(name) {
    state.view = name;
    document.querySelectorAll("[data-view]").forEach((item) => {
      item.classList.toggle("active", item.dataset.view === name);
    });
    document.querySelectorAll(".view").forEach((item) => {
      item.classList.toggle("active", item.id === "view-" + name);
    });
    $("view-title").textContent = titles[name][0];
    $("view-sub").textContent = titles[name][1];
    if (name === "artifacts") loadArtifacts();
  }

  document.querySelectorAll("[data-view]").forEach((item) => {
    item.addEventListener("click", () => showView(item.dataset.view));
  });

  function priceClass(value) {
    const number = Number(value);
    if (!Number.isFinite(number) || number === 0) return "flat";
    return number > 0 ? "up" : "down";
  }

  function pct(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "—";
    return (number > 0 ? "+" : "") + number.toFixed(2) + "%";
  }

  function money(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "—";
    if (Math.abs(number) >= 1e8) return (number / 1e8).toFixed(0) + " 亿";
    if (Math.abs(number) >= 1e4) return (number / 1e4).toFixed(0) + " 万";
    return number.toFixed(2);
  }

  function artifactUrl(item) {
    const suffix = String(item.mime || "").startsWith("image/") ? "png" : "html";
    return `${MOUNT}/artifact/${item.id}.${suffix}`;
  }

  async function loadHealth() {
    try {
      const info = await api("/health");
      $("health-dot").className = "dot ok";
      $("health-text").textContent = "QWeb 宿主在线 · v" + info.version;
      $("health-sub").textContent = info.render_enabled ? "图片渲染已启用" : "HTML 模式（PNG 可选）";
      $("host-pill").textContent = "QWeb 宿主";
      $("host-pill").className = "pill ok";
    } catch (error) {
      $("health-dot").className = "dot bad";
      $("health-text").textContent = "QWeb 宿主未授权";
      $("health-sub").textContent = String(error.message || error);
    }
  }

  async function loadOverview() {
    try {
      const payload = await api("/overview");
      renderOverview(payload.data);
      $("overview-time").textContent = "数据时间 " + (payload.data.as_of || "—") + " · 点击下方按钮打开图像版";
    } catch (error) {
      toast("大盘数据加载失败：" + error.message, true);
    }
  }

  function renderOverview(data) {
    const indices = data.indices || [];
    $("index-grid").innerHTML = indices.slice(0, 12).map((item) => {
      const tone = priceClass(item.change_pct);
      return `<article class="index-card ${tone}">
        <div class="index-name">${esc(item.label || item.name)}</div>
        <div class="index-price">${esc(item.price)}</div>
        <div class="index-change ${tone}">${pct(item.change_pct)}</div>
        ${item.spark || ""}
        <div class="index-meta">${esc(item.code || "")} · ${esc(item.provider || "")}</div>
      </article>`;
    }).join("") || `<div class="index-meta">暂无指数数据</div>`;

    const breadth = data.breadth || {};
    const total = (breadth.up || 0) + (breadth.down || 0) + (breadth.flat || 0);
    const up = total ? (breadth.up / total) * 100 : 0;
    const down = total ? (breadth.down / total) * 100 : 0;
    const flat = Math.max(0, 100 - up - down);
    $("stat-up").textContent = breadth.up ?? "—";
    $("stat-down").textContent = breadth.down ?? "—";
    $("stat-limit-up").textContent = breadth.limit_up ?? "—";
    $("stat-limit-down").textContent = breadth.limit_down ?? "—";
    const turnover = data.turnover || {};
    $("stat-turnover").textContent = money(turnover.amount);
    const north = data.northbound || {};
    $("stat-north").textContent = Number.isFinite(Number(north.sh_net_yi))
      ? (Number(north.sh_net_yi) + Number(north.sz_net_yi || 0)).toFixed(2) + " 亿"
      : "—";
    $("breadth-bar").innerHTML = `<span class="up" style="width:${up.toFixed(1)}%"></span>
      <span class="down" style="width:${down.toFixed(1)}%"></span>
      <span class="flat" style="width:${flat.toFixed(1)}%"></span>`;
    $("breadth-legend").innerHTML = `<span class="up">上涨 ${breadth.up ?? 0}</span>
      <span>平盘 ${breadth.flat ?? 0}</span><span class="down">下跌 ${breadth.down ?? 0}</span>`;

    const rows = ((data.industries || {}).rise || []).slice(0, 10);
    $("industry-table").querySelector("tbody").innerHTML = rows.map((row) =>
      `<tr><td>${esc(row.name)}</td>
       <td class="num ${priceClass(row.change_pct)}">${pct(row.change_pct)}</td>
       <td>${esc(row.lead_name || "")}</td>
       <td class="num">${pct(row.lead_change_pct)}</td></tr>`
    ).join("") || `<tr><td colspan="4">暂无板块数据</td></tr>`;
  }

  $("open-overview-chart").addEventListener("click", () => {
    window.open("api/chart/html?type=overview", "_blank", "noopener");
  });

  async function searchStock() {
    const query = $("stock-query").value.trim();
    if (!query) { toast("请输入股票代码或名称", true); return; }
    const period = $("stock-period").value;
    const chartType = (period === "分时" || period === "五日") ? "intraday" : "kline";
    const chartPeriod = period === "五日" ? "日k" : period;
    const ndays = period === "五日" ? 5 : 1;

    try {
      const payload = await api("/quote?q=" + encodeURIComponent(query));
      const quote = payload.data;
      state.lastQuote = quote;
      $("quote-name").textContent = quote.display_name || quote.name || query;
      $("quote-source").textContent = (quote.provider || "") + " · " + (quote.as_of || "");
      $("quote-price").textContent = quote.price ?? "—";
      const tone = priceClass(quote.change_pct);
      $("quote-change").textContent = pct(quote.change_pct);
      $("quote-change").className = tone;
      $("quote-table").innerHTML = [
        ["今开", quote.open], ["最高", quote.high], ["最低", quote.low],
        ["昨收", quote.prev_close], ["成交额", money(quote.amount)],
        ["换手率", quote.turnover_rate != null ? Number(quote.turnover_rate).toFixed(2) + "%" : "—"],
        ["PE / PB", `${quote.pe ?? "—"} / ${quote.pb ?? "—"}`],
        ["总市值", money(quote.market_cap)],
      ].map(([key, value]) => `<tr><td>${key}</td><td class="num">${esc(value)}</td></tr>`).join("");
      $("tech-output").textContent = "";
    } catch (error) {
      toast("行情查询失败：" + error.message, true);
    }

    const chartUrl = `api/chart/html?type=${chartType}&q=${encodeURIComponent(query)}&period=${encodeURIComponent(chartPeriod)}&ndays=${ndays}`;
    $("chart-frame").src = chartUrl;
    $("chart-open").href = chartUrl;
    $("chart-title").textContent = query + " · " + period;
  }

  $("stock-search").addEventListener("click", searchStock);
  $("stock-query").addEventListener("keydown", (event) => { if (event.key === "Enter") searchStock(); });
  document.querySelectorAll(".quick-tags button").forEach((button) => {
    button.addEventListener("click", () => { $("stock-query").value = button.dataset.q; searchStock(); });
  });

  $("technical-btn").addEventListener("click", async () => {
    const query = $("stock-query").value.trim();
    if (!query) { toast("请输入股票代码或名称", true); return; }
    $("tech-output").textContent = "计算中…";
    try {
      const payload = await api("/command", {
        method: "POST",
        body: JSON.stringify({ text: "技术分析 " + query }),
      });
      $("tech-output").textContent = payload.text || "暂无结果";
    } catch (error) {
      $("tech-output").textContent = "技术分析失败：" + error.message;
    }
  });

  $("add-watch").addEventListener("click", async () => {
    if (!state.lastQuote) { toast("先查询一只股票", true); return; }
    try {
      await api("/watchlist", {
        method: "POST",
        body: JSON.stringify({ query: state.lastQuote.code }),
      });
      toast("已加入自选");
    } catch (error) { toast("加入自选失败：" + error.message, true); }
  });

  async function loadArtifacts() {
    try {
      const payload = await api("/artifacts");
      const items = payload.items || [];
      $("artifact-grid").innerHTML = items.map((item) => {
        const time = item.created_at ? new Date(item.created_at * 1000).toLocaleString() : "";
        return `<div class="artifact-item">
          <b>${esc(item.title || item.id)}</b>
          <span>${esc(item.kind || "")} · ${esc(time)}</span>
          <a href="${esc(artifactUrl(item))}" target="_blank" rel="noopener">打开 →</a>
        </div>`;
      }).join("") || `<p class="hint">还没有制品。让 AI 画一张图或写一份报告吧。</p>`;
    } catch (error) {
      toast("制品加载失败：" + error.message, true);
    }
  }

  $("refresh-btn").addEventListener("click", () => {
    loadHealth();
    if (state.view === "market") loadOverview();
    if (state.view === "artifacts") loadArtifacts();
    if (state.view === "stock" && $("stock-query").value.trim()) searchStock();
    toast("已刷新");
  });

  async function boot() {
    await loadHealth();
    await loadOverview();
  }

  boot();
})();
