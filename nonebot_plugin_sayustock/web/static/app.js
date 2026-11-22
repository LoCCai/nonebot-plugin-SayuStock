(() => {
  "use strict";

  const API = new URL("api", document.baseURI).pathname.replace(/\/+$/, "");
  const MOUNT = new URL(".", document.baseURI).pathname.replace(/\/+$/, "");
  const SESSION_URL = "/qweb/api/v1/auth/session";

  const PERIODS = Object.freeze([
    { value: "5k", label: "5 分钟 K" },
    { value: "15k", label: "15 分钟 K" },
    { value: "30k", label: "30 分钟 K" },
    { value: "60k", label: "60 分钟 K" },
    { value: "日k", label: "日 K" },
    { value: "周k", label: "周 K" },
    { value: "月k", label: "月 K" },
    { value: "季k", label: "季 K" },
    { value: "半年k", label: "半年 K" },
    { value: "年k", label: "年 K" },
  ]);

  const VIEW_META = Object.freeze({
    market: ["市场总览", "指数、涨跌分布、成交额与板块强弱"],
    discover: ["选股与资讯", "条件选股、资金榜单与多源财经快讯"],
    research: ["个股研究", "全周期行情、技术分析、财务、估值与报告"],
    watchlist: ["我的自选", "自选股与最新行情"],
    artifacts: ["图表与报告", "当前账户的 HTML / PNG 制品"],
    workbench: ["研究与模拟盘", "专题图表、策略与账户"],
    help: ["命令与帮助", "指令目录"],
  });

  const state = {
    view: "market",
    query: "",
    lastQuote: null,
    chart: null,
    overviewLoaded: false,
    preview: null,
    csrfValue: "",
    csrfAt: 0,
  };

  const previews = new Map();
  let previewSequence = 0;

  const $ = (id) => document.getElementById(id);

  function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[character]));
  }

  function numeric(value) {
    if (typeof value === "number") return Number.isFinite(value) ? value : null;
    if (typeof value === "string" && value.trim() !== "") {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : null;
    }
    return null;
  }

  function plain(value) {
    if (value === null || value === undefined || value === "") return "—";
    return String(value);
  }

  function fmtNumber(value, digits = 2) {
    const number = numeric(value);
    return number === null ? "—" : number.toFixed(digits);
  }

  function fmtInt(value) {
    const number = numeric(value);
    return number === null ? "—" : number.toLocaleString("zh-CN");
  }

  function fmtPct(value) {
    const number = numeric(value);
    if (number === null) return "—";
    return `${number > 0 ? "+" : ""}${number.toFixed(2)}%`;
  }

  function fmtMoney(value) {
    const number = numeric(value);
    if (number === null) return "—";
    const absolute = Math.abs(number);
    if (absolute >= 1e12) return `${(number / 1e12).toFixed(2)} 万亿`;
    if (absolute >= 1e8) return `${(number / 1e8).toFixed(2)} 亿`;
    if (absolute >= 1e4) return `${(number / 1e4).toFixed(2)} 万`;
    return number.toFixed(2);
  }

  function fmtVolume(value) {
    const number = numeric(value);
    if (number === null) return "—";
    const absolute = Math.abs(number);
    if (absolute >= 1e8) return `${(number / 1e8).toFixed(2)} 亿股`;
    if (absolute >= 1e4) return `${(number / 1e4).toFixed(2)} 万股`;
    return `${number.toFixed(0)} 股`;
  }

  function fmtSignedYi(value) {
    const number = numeric(value);
    if (number === null) return "—";
    return `${number > 0 ? "+" : ""}${number.toFixed(2)} 亿`;
  }

  function tone(value) {
    const number = numeric(value);
    if (number === null) return "flat";
    if (number > 0) return "up";
    if (number < 0) return "down";
    return "flat";
  }

  function fmtTime(seconds) {
    const number = numeric(seconds);
    if (number === null || number <= 0) return "—";
    return new Date(number * 1000).toLocaleString("zh-CN", { hour12: false });
  }

  function fmtBytes(value) {
    const number = numeric(value);
    if (number === null) return "—";
    if (number >= 1024 * 1024) return `${(number / (1024 * 1024)).toFixed(1)} MB`;
    if (number >= 1024) return `${(number / 1024).toFixed(1)} KB`;
    return `${number} B`;
  }

  function periodLabel(period) {
    return PERIODS.find((item) => item.value === period)?.label || plain(period);
  }

  function toast(message, isError = false) {
    const node = $("toast");
    node.textContent = String(message || "");
    node.className = `toast show${isError ? " error" : ""}`;
    window.clearTimeout(toast.timer);
    toast.timer = window.setTimeout(() => {
      node.className = "toast";
    }, 3200);
  }

  async function csrfToken(force = false) {
    if (!force && state.csrfValue && Date.now() - state.csrfAt < 120000) {
      return state.csrfValue;
    }
    const response = await fetch(SESSION_URL, {
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error("无法读取宿主登录会话");
    const session = await response.json();
    const token = typeof session?.csrf_token === "string" ? session.csrf_token.trim() : "";
    if (!token) throw new Error("宿主会话缺少 CSRF 令牌，请重新登录七七");
    state.csrfValue = token;
    state.csrfAt = Date.now();
    return token;
  }

  function requestError(payload, status) {
    if (payload && typeof payload.detail === "string" && payload.detail.trim()) return payload.detail;
    if (payload && Array.isArray(payload.detail) && payload.detail.length) {
      const detail = payload.detail
        .map((item) => (item && typeof item.msg === "string" ? item.msg : ""))
        .filter(Boolean)
        .join("；");
      if (detail) return detail;
    }
    if (payload && typeof payload.message === "string" && payload.message.trim()) return payload.message;
    return `HTTP ${status}`;
  }

  async function api(path, options = {}, allowRetry = true) {
    const method = String(options.method || "GET").toUpperCase();
    const headers = { Accept: "application/json", ...(options.headers || {}) };
    if (options.body !== undefined && !headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }
    if (method !== "GET" && method !== "HEAD") {
      headers["X-CSRF-Token"] = await csrfToken();
    }
    const response = await fetch(API + path, {
      ...options,
      method,
      headers,
      credentials: "same-origin",
      cache: "no-store",
    });
    const contentType = String(response.headers.get("content-type") || "");
    let payload = null;
    if (contentType.includes("application/json")) {
      payload = await response.json().catch(() => null);
    } else {
      payload = await response.text().catch(() => "");
    }
    if (!response.ok) {
      if (response.status === 401) toast("QWeb 宿主未识别当前成员身份", true);
      if (response.status === 403 && method !== "GET" && allowRetry) {
        state.csrfValue = "";
        state.csrfAt = 0;
        return api(path, options, false);
      }
      throw new Error(requestError(payload, response.status));
    }
    return payload;
  }

  function resolveOutput(target) {
    if (target instanceof Element) return target;
    if (typeof target === "string") return document.querySelector(target);
    return null;
  }

  function outputLoading(target, command) {
    const node = resolveOutput(target);
    if (!node) return;
    node.hidden = false;
    node.classList.remove("is-error");
    node.innerHTML = `
      <div class="output-loading">
        <span class="spinner" aria-hidden="true"></span>
        <span>正在执行 <code>${esc(command)}</code></span>
      </div>`;
  }

  function outputError(target, message, command = "") {
    const node = resolveOutput(target);
    if (!node) return;
    node.hidden = false;
    node.classList.add("is-error");
    node.innerHTML = `
      <div class="output-head">
        <span class="output-label">执行失败</span>
        ${command ? `<code class="output-command">${esc(command)}</code>` : ""}
      </div>
      <p class="output-error">${esc(message)}</p>`;
  }

  function registerPreview(item) {
    const key = `preview-${++previewSequence}`;
    previews.set(key, item);
    return key;
  }

  function artifactPreview(item) {
    const isImage = String(item.mime || "").startsWith("image/");
    return {
      kind: isImage ? "image" : "html",
      url: item.url || artifactUrl(item),
      title: item.title || "制品预览",
      sub: `${isImage ? "PNG" : "HTML"} · ${item.kind || ""} · ${fmtTime(item.created_at)}`,
    };
  }

  function previewButton(label, item) {
    const key = registerPreview(item);
    return `<button class="ghost artifact-action" type="button" data-preview-key="${esc(key)}">${esc(label)}</button>`;
  }

  function renderLedgerTable(data) {
    const schemas = {
      accounts: {name: "盘名", strategy_id: "策略", cash: "现金", total_equity: "净值", total_pnl_pct: "累计收益 (%)"},
      trades: {executed_at: "成交时间", name: "标的", side: "方向", qty: "股数", price: "成交价", fee: "费用"},
      strategies: {id: "策略编号", name: "策略"},
      events: {title: "事件", severity: "影响等级", direction: "方向", result: "进展"}
    };
    for (const [key, fields] of Object.entries(schemas)) {
      const rows = data[key];
      if (!Array.isArray(rows) || !rows.length || !rows.every(r => r && typeof r === "object")) continue;
      const cols = Object.keys(fields).filter(k => rows.some(row => row[k] != null));
      if (!cols.length) continue;
      const labels = {buy: "买入", sell: "卖出"};
      return `<div class="result-table-wrap"><table><thead><tr>${cols.map(k => `<th>${fields[k]}</th>`).join("")}</tr></thead><tbody>${rows.map(row =>
        `<tr>${cols.map(k => `<td>${esc(String(k === "side" ? labels[row[k]] || row[k] : row[k] ?? "—"))}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
    }
    return "";
  }

  function renderCommand(payload, command, target) {
    const node = resolveOutput(target);
    if (!node) return;
    node.hidden = false;
    node.classList.remove("is-error");
    const artifacts = Array.isArray(payload?.artifacts) ? payload.artifacts : [];
    const actions = artifacts.map((item) => {
      const isImage = String(item.mime || "").startsWith("image/");
      return previewButton(isImage ? "PNG 预览" : "HTML 预览", artifactPreview(item));
    });
    const image = typeof payload?.image === "string" ? payload.image : "";
    if (image) {
      actions.push(previewButton("PNG 预览", {
        kind: "image",
        url: image,
        title: "命令生成图片",
        sub: command,
      }));
    }
    const text = typeof payload?.text === "string" && payload.text.trim() ? payload.text : "命令已执行，返回空文本。";
    const report = artifacts.find(item => String(item.mime || "").startsWith("text/html"));
    const details = payload?.structured || {};
    const table = renderLedgerTable(details);
    const inline = report ? `<iframe class="inline-report" sandbox="allow-same-origin" referrerpolicy="no-referrer" loading="lazy" title="${esc(report.title || command)}" src="${esc(artifactPreview(report).url)}"></iframe>` : "";
    node.innerHTML = `
      <div class="output-head">
        <span class="output-label">任务结果</span>
        <code class="output-command">${esc(command)}</code>
        <button class="icon-btn" type="button" data-output-close aria-label="关闭结果">×</button>
      </div>
      ${inline || table}
      ${inline || table ? `<details class="result-details"><summary>文字详情</summary><pre class="output-text">${esc(text)}</pre></details>` : `<pre class="output-text">${esc(text)}</pre>`}
      ${actions.length ? `<div class="output-actions">${actions.join("")}</div>` : ""}`;
  }

  async function runCommand(command, target, options = {}) {
    const text = String(command || "").trim();
    if (!text) {
      toast("请输入命令", true);
      return null;
    }
    if (text.trim() === "清空自选") {
      const dialog = $("watch-confirm");
      if (dialog.open) return null;
      dialog.returnValue = "";
      const answer = new Promise(resolve => dialog.addEventListener("close", () => resolve(dialog.returnValue), {once:true}));
      dialog.showModal();
      if (await answer !== "clear") return null;
    }
    outputLoading(target, text);
    try {
      const payload = await api("/command", {
        method: "POST",
        body: JSON.stringify({ text }),
      });
      if (payload?.structured?.menu_url === "/qweb/features/sayustock") {
        window.top.location.assign("/qweb/features/sayustock");
        return payload;
      }
      renderCommand(payload, text, target);
      if (options.scroll !== false) {
        const node = resolveOutput(target);
        const panel = node?.closest(".panel");
        (panel || node)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
      return payload;
    } catch (error) {
      const message = String(error?.message || error);
      outputError(target, message, text);
      toast(`命令失败：${message}`, true);
      return null;
    }
  }

  function artifactUrl(item) {
    const suffix = String(item.mime || "").startsWith("image/") ? "png" : "html";
    return `${MOUNT}/artifact/${item.id}.${suffix}`;
  }

  function chartColors() {
    const context = document.createElement("canvas").getContext("2d", {willReadFrequently:true});
    const style = getComputedStyle(document.documentElement);
    const names = ["app-bg", "surface", "ink", "muted", "brand", "warm", "secondary", "line-strong"];
    const hex = names.map(name => {
      context.fillStyle = style.getPropertyValue("--app-bg").trim();
      context.fillRect(0, 0, 1, 1);
      const value = style.getPropertyValue("--" + name).trim();
      if (value) context.fillStyle = value;
      context.fillRect(0, 0, 1, 1);
      return Array.from(context.getImageData(0, 0, 1, 1).data.slice(0, 3), v => v.toString(16).padStart(2, "0")).join("");
    });
    return hex.join(",");
  }

  function chartUrl(kind, chart) {
    const params = new URLSearchParams({
      type: chart.type,
      q: chart.q,
      period: chart.period || "日k",
      ndays: String(chart.ndays || 1),
    });
    if (kind === "html") {
      params.set("colors", chartColors());
      params.set("mode", ["light","sakura"].includes(document.documentElement.dataset.theme) ? "light" : "dark");
    }
    return `${API}/chart/${kind}?${params.toString()}`;
  }

  function withCacheBuster(url) {
    if (String(url).startsWith("data:")) return url;
    return `${url}${url.includes("?") ? "&" : "?"}_=${Date.now()}`;
  }

  function renderPreview(item) {
    const dialog = $("preview-dialog");
    const body = $("preview-body");
    state.preview = item;
    $("preview-title").textContent = item.title || "预览";
    $("preview-sub").textContent = item.sub || "";
    body.replaceChildren();
    if (item.kind === "image") {
      const image = document.createElement("img");
      image.className = "preview-image";
      image.alt = item.title || "图片预览";
      image.src = withCacheBuster(item.url);
      body.appendChild(image);
    } else {
      const frame = document.createElement("iframe");
      frame.className = "preview-frame";
      frame.title = item.title || "HTML 预览";
      frame.setAttribute("sandbox", "allow-same-origin");
      frame.setAttribute("referrerpolicy", "no-referrer");
      frame.src = withCacheBuster(item.url);
      body.appendChild(frame);
    }
    if (!dialog.open) dialog.showModal();
  }

  function openPreview(key) {
    const item = previews.get(key);
    if (!item) {
      toast("预览已失效，请重新执行任务", true);
      return;
    }
    renderPreview(item);
  }

  function openChartPreview(kind, chart = state.chart) {
    if (!chart) {
      toast("先生成一张图表", true);
      return;
    }
    const item = {
      kind: kind === "png" ? "image" : "html",
      url: chartUrl(kind, chart),
      title: chart.title || "行情图表",
      sub: `${kind === "png" ? "PNG" : "HTML"} · ${periodLabel(chart.period)} · ${chart.type === "overview" ? "大盘概览" : chart.q}`,
    };
    renderPreview(item);
  }

  function setupPreviewDialog() {
    const dialog = $("preview-dialog");
    $("preview-close").addEventListener("click", () => dialog.close());
    $("preview-reload").addEventListener("click", () => {
      if (state.preview) renderPreview(state.preview);
    });
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
    dialog.addEventListener("close", () => {
      $("preview-body").replaceChildren();
    });
  }

  function showView(name) {
    if (!VIEW_META[name]) return;
    state.view = name;
    document.querySelectorAll("[data-view]").forEach((item) => {
      const active = item.dataset.view === name;
      item.classList.toggle("is-active", active);
      if (active) item.setAttribute("aria-current", "page");
      else item.removeAttribute("aria-current");
    });
    document.querySelectorAll(".view").forEach((item) => {
      item.classList.toggle("is-active", item.id === `view-${name}`);
    });
    const meta = VIEW_META[name];
    $("view-title").textContent = meta[0];
    $("view-sub").textContent = meta[1];
    if (name === "market" && !state.overviewLoaded) void loadOverview();
    if (name === "workbench") void loadFeatures();
    if (name === "watchlist") void loadWatchlist();
    if (name === "artifacts") void loadArtifacts();
  }

  function showDiscover(name) {
    document.querySelectorAll("[data-discover]").forEach((item) => {
      const active = item.dataset.discover === name;
      item.classList.toggle("is-active", active);
      item.setAttribute("aria-selected", active ? "true" : "false");
    });
    document.querySelectorAll(".discover-pane").forEach((item) => {
      item.classList.toggle("is-active", item.id === `discover-${name}`);
    });
  }

  async function loadHealth() {
    try {
      const info = await api("/health");
      $("health-dot").className = "dot ok";
      $("health-text").textContent = `QWeb 宿主在线 · v${plain(info.version)}`;
      $("health-sub").textContent = info.render_enabled ? "图片渲染已启用" : "HTML 模式（PNG 按需渲染）";
      $("host-pill").textContent = "QWeb 宿主已连接";
      $("host-pill").className = "pill ok";
    } catch (error) {
      $("health-dot").className = "dot bad";
      $("health-text").textContent = "QWeb 宿主未授权";
      $("health-sub").textContent = String(error?.message || error);
      $("host-pill").textContent = "宿主连接异常";
      $("host-pill").className = "pill bad";
    }
  }

  async function loadOverview() {
    try {
      const payload = await api("/overview");
      renderOverview(payload.data, payload);
      state.overviewLoaded = true;
    } catch (error) {
      toast(`大盘数据加载失败：${error?.message || error}`, true);
      $("overview-time").textContent = "行情获取失败，请稍后刷新";
    }
  }

  function renderOverview(data, presentation) {
    const indices = Array.isArray(data.indices) ? data.indices : [];
    $("index-grid").innerHTML = indices.map((item) => {
      const direction = tone(item.change_pct);
      const spark = typeof item.spark === "string" && item.spark.includes("<svg") ? item.spark : "";
      return `<article class="index-card ${direction}">
        <div class="index-name">${esc(plain(item.label || item.name))}</div>
        <div class="index-price">${esc(fmtNumber(item.price, 2))}</div>
        <div class="index-change ${direction}">${esc(fmtPct(item.change_pct))}</div>
        ${spark}
        <div class="index-meta">${esc(plain(item.code))} · ${esc(plain(item.provider))}</div>
      </article>`;
    }).join("") || `<p class="hint">暂无指数数据</p>`;

    const breadth = data.breadth && typeof data.breadth === "object" ? data.breadth : {};
    const up = numeric(breadth.up);
    const down = numeric(breadth.down);
    const flat = numeric(breadth.flat);
    const complete = up !== null && down !== null && flat !== null;
    $("stat-up").textContent = up === null ? "—" : fmtInt(up);
    $("stat-down").textContent = down === null ? "—" : fmtInt(down);
    $("stat-flat").textContent = flat === null ? "—" : fmtInt(flat);
    $("stat-limit-up").textContent = numeric(breadth.limit_up) === null ? "—" : fmtInt(breadth.limit_up);
    $("stat-limit-down").textContent = numeric(breadth.limit_down) === null ? "—" : fmtInt(breadth.limit_down);
    $("stat-turnover").textContent = fmtMoney(data.turnover && data.turnover.amount);
    $("stat-north").textContent = northboundText(data.northbound);
    $("breadth-note").textContent = complete ? "单位 · 家" : "涨跌数据不完整";
    $("breadth-legend").innerHTML = [
      `<span class="up">上涨 ${esc(up === null ? "—" : fmtInt(up))}</span>`,
      `<span class="flat">平盘 ${esc(flat === null ? "—" : fmtInt(flat))}</span>`,
      `<span class="down">下跌 ${esc(down === null ? "—" : fmtInt(down))}</span>`,
    ].join("");
    $("breadth-bar").innerHTML = presentation.breadth_svg;
    $("overview-mood").textContent = presentation.mood;
    const moodImage = $("market-mood-image");
    moodImage.hidden = !presentation.mood_image;
    if (presentation.mood_image) moodImage.src = presentation.mood_image;
    else moodImage.removeAttribute("src");
    $("turnover-note").textContent = data.turnover_note || "";
    const flow = $("main-flow");
    flow.hidden = !data.main_flow_uri;
    $("main-flow-note").hidden = !flow.hidden;
    if (data.main_flow_uri) flow.src = data.main_flow_uri;
    else flow.removeAttribute("src");

    const warnings = Array.isArray(data.warnings) ? data.warnings : [];
    const warningNode = $("overview-warnings");
    warningNode.hidden = warnings.length === 0;
    warningNode.innerHTML = warnings.slice(0, 6).map((warning) => `<p>⚠ ${esc(warning)}</p>`).join("");

    $("overview-time").textContent = data.as_of
      ? `${plain(data.session_label)} · 生成于 ${plain(data.as_of)}`
      : "数据时间 —";
    $("overview-provider").textContent = `数据源 ${plain(presentation.sources)}`;
    $("industry-table").querySelector("tbody").innerHTML = boardRows((data.industries || {}).rise);
    $("concept-table").querySelector("tbody").innerHTML = boardRows((data.concepts || {}).rise);
    $("industry-fall").querySelector("tbody").innerHTML = boardRows(data.industries?.fall, true);
    $("concept-fall").querySelector("tbody").innerHTML = boardRows(data.concepts?.fall, true);
    state.chart = {
      type: "overview",
      q: "上证指数",
      period: "日k",
      ndays: 1,
      title: "A股大盘概览",
    };
  }

  function northboundText(northbound) {
    if (!northbound || typeof northbound !== "object") return "—";
    const sh = numeric(northbound.sh_net_yi);
    const sz = numeric(northbound.sz_net_yi);
    if (sh === null && sz === null) return "—";
    if (sh !== null && sz !== null) {
      const total = sh + sz;
      return `${total > 0 ? "+" : ""}${total.toFixed(2)} 亿`;
    }
    const known = sh !== null ? `沪 ${fmtSignedYi(sh)}` : `深 ${fmtSignedYi(sz)}`;
    return `${known}（另一侧缺失）`;
  }

  function boardRows(rows, falling = false, limit = 20) {
    const list = Array.isArray(rows) ? rows.slice(0, limit) : [];
    if (!list.length) return `<tr><td colspan="4">暂无板块数据</td></tr>`;
    return list.map((row) => `<tr>
      <td>${esc(plain(row.name))}</td>
      <td class="num ${tone(row.change_pct)}">${esc(fmtPct(row.change_pct))}</td>
      <td>${esc(plain(falling ? row.fall_name : row.lead_name))}</td>
      <td class="num ${tone(falling ? row.fall_change_pct : row.lead_change_pct)}">${esc(fmtPct(falling ? row.fall_change_pct : row.lead_change_pct))}</td>
    </tr>`).join("");
  }

  function valueOf(id) {
    const node = $(id);
    return node ? node.value.trim() : "";
  }

  function setValue(id, value) {
    const node = $(id);
    if (node) node.value = value;
  }

  function splitQueries(raw) {
    return String(raw || "")
      .replace(/[，、]/g, " ")
      .split(/\s+/)
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function currentPeriodSelect(select, fallback = "日k") {
    return select && PERIODS.some((item) => item.value === select.value) ? select.value : fallback;
  }

  function stockConfig() {
    const query = valueOf("stock-query");
    const mode = $("stock-mode")?.value === "intraday" ? "intraday" : "kline";
    const period = currentPeriodSelect($("stock-period"));
    const ndays = Math.max(1, Math.min(5, Number($("stock-days")?.value) || 1));
    return {
      type: mode,
      q: query,
      period: mode === "intraday" ? "日k" : period,
      ndays: mode === "intraday" ? ndays : 1,
    };
  }

  function chartLabel(chart) {
    if (chart.type === "intraday") return `${chart.ndays} 日分时`;
    return periodLabel(chart.period);
  }

  function syncStockMode() {
    const intraday = $("stock-mode")?.value === "intraday";
    const daysField = $("stock-days-field");
    const periodField = $("stock-period-field");
    if (daysField) daysField.hidden = !intraday;
    if (periodField) periodField.hidden = intraday;
  }

  async function searchStock() {
    const chart = stockConfig();
    if (!chart.q) {
      toast("请输入股票代码或名称", true);
      $("stock-query")?.focus();
      return;
    }
    state.query = chart.q;
    fillResearchDefaults(chart.q);
    chart.title = `${chart.q} · ${chartLabel(chart)}`;
    state.chart = chart;
    $("chart-title").textContent = chart.title;
    $("chart-meta").textContent = "图表加载中…";
    $("chart-empty").hidden = true;
    $("chart-frame").src = chartUrl("html", chart);

    const jobs = [api(`/quote?q=${encodeURIComponent(chart.q)}`)];
    if (chart.type === "kline") {
      jobs.push(api(`/kline?q=${encodeURIComponent(chart.q)}&period=${encodeURIComponent(chart.period)}`));
    }
    const results = await Promise.allSettled(jobs);
    const quoteResult = results[0];
    if (quoteResult.status === "fulfilled") {
      updateQuote(quoteResult.value?.data || {});
    } else {
      toast(`行情查询失败：${quoteResult.reason?.message || quoteResult.reason}`, true);
      $("quote-name").textContent = chart.q;
      $("quote-source").textContent = "行情获取失败";
    }
    if (results[1] && results[1].status === "fulfilled") {
      updateKlineMeta(results[1].value?.data || {}, chart);
    } else if (chart.type === "intraday") {
      $("chart-meta").textContent = `${chartLabel(chart)} · 图表通过后端渲染`;
    } else {
      $("chart-meta").textContent = `${chartLabel(chart)} · K 线摘要暂不可用`;
    }
  }

  function updateQuote(quote) {
    state.lastQuote = quote;
    $("quote-name").textContent = plain(quote.display_name || quote.name || state.query);
    const source = [quote.provider, quote.as_of].filter((item) => item !== null && item !== undefined && item !== "");
    $("quote-source").textContent = source.length ? source.join(" · ") : "数据源 —";
    $("quote-price").textContent = fmtNumber(quote.price, 2);
    const change = $("quote-change");
    change.textContent = fmtPct(quote.change_pct);
    change.className = `quote-change ${tone(quote.change_pct)}`;
    const rows = [
      ["代码", plain(quote.code)],
      ["今开", fmtNumber(quote.open, 2)],
      ["最高", fmtNumber(quote.high, 2)],
      ["最低", fmtNumber(quote.low, 2)],
      ["昨收", fmtNumber(quote.prev_close, 2)],
      ["成交额", fmtMoney(quote.amount)],
      ["成交量", fmtVolume(quote.volume)],
      ["换手率", numeric(quote.turnover_rate) === null ? "—" : `${numeric(quote.turnover_rate).toFixed(2)}%`],
      ["PE / PB", `${fmtNumber(quote.pe, 2)} / ${fmtNumber(quote.pb, 2)}`],
      ["总市值", fmtMoney(quote.market_cap)],
      ["流通市值", fmtMoney(quote.float_market_cap)],
      ["行业", plain(quote.industry)],
      ["涨停 / 跌停", `${fmtNumber(quote.limit_up, 2)} / ${fmtNumber(quote.limit_down, 2)}`],
    ];
    $("quote-table").querySelector("tbody").innerHTML = rows.map(([label, value]) =>
      `<tr><td>${esc(label)}</td><td class="num">${esc(value)}</td></tr>`
    ).join("");
  }

  function updateKlineMeta(data, chart) {
    const bars = Array.isArray(data.bars) ? data.bars : [];
    const last = bars.length ? bars[bars.length - 1] : null;
    const provider = plain(data.provider);
    if (last) {
      $("chart-meta").textContent = `${chartLabel(chart)} · ${bars.length} 根 · 最新收盘 ${fmtNumber(last.close, 2)} · ${provider}`;
    } else {
      $("chart-meta").textContent = `${chartLabel(chart)} · 暂无 K 线数据 · ${provider}`;
    }
  }

  function fillResearchDefaults(query) {
    if (!query) return;
    ["technical-query", "financial-query", "valuation-query", "report-query"].forEach((id) => {
      setValue(id, query);
    });
  }

  function stockCommandText(query) {
    const chart = stockConfig();
    if (!query) return null;
    if (chart.type === "intraday") {
      if (chart.ndays === 1) return `个股 ${query}`;
      if (chart.ndays === 5) return `个股 五日 ${query}`;
      return null;
    }
    return `个股 ${chart.period} ${query}`;
  }

  function runStockCommand() {
    const query = valueOf("stock-query") || state.query;
    if (!query) {
      toast("请先输入标的", true);
      return;
    }
    const command = stockCommandText(query);
    if (!command) {
      toast("2–4 日分时请直接使用图表；命令模式支持当日/五日分时与全部 K 线周期", true);
      return;
    }
    void runCommand(command, "#stock-output");
  }

  function screenCommand() {
    const capMin = valueOf("screen-cap-min");
    const capMax = valueOf("screen-cap-max");
    const peMax = valueOf("screen-pe-max");
    const changeMin = valueOf("screen-change-min");
    const changeMax = valueOf("screen-change-max");
    const industry = valueOf("screen-industry");
    const tokens = [];
    if (capMin && capMax) {
      const min = numeric(capMin);
      const max = numeric(capMax);
      if (min !== null && max !== null && min > max) {
        toast("最小市值不能大于最大市值", true);
        return null;
      }
      tokens.push(`市值${capMin}-${capMax}`);
    } else if (capMin) {
      tokens.push(`市值>${capMin}`);
    } else if (capMax) {
      tokens.push(`市值<${capMax}`);
    }
    if (peMax) tokens.push(`PE<${peMax}`);
    if (changeMin) tokens.push(`涨跌幅>${changeMin}`);
    if (changeMax) tokens.push(`涨跌幅<${changeMax}`);
    if (industry) {
      if (/\s/.test(industry)) {
        toast("行业关键字不能包含空格", true);
        return null;
      }
      tokens.push(`行业 ${industry}`);
    }
    if (!tokens.length) {
      toast("至少填写一个筛选条件", true);
      return null;
    }
    return `自动选股 ${tokens.join(" ")}`;
  }

  function presetScreen(kind) {
    if (kind === "mid") {
      setValue("screen-cap-min", "50");
      setValue("screen-cap-max", "300");
      setValue("screen-pe-max", "40");
      setValue("screen-change-min", "1");
      setValue("screen-change-max", "");
      setValue("screen-industry", "");
    } else if (kind === "lowpe") {
      setValue("screen-cap-min", "100");
      setValue("screen-cap-max", "");
      setValue("screen-pe-max", "15");
      setValue("screen-change-min", "");
      setValue("screen-change-max", "");
      setValue("screen-industry", "");
    }
    toast(kind === "mid" ? "已填入中小市值成长条件" : "已填入低估值条件");
  }

  function runCompare() {
    const queries = splitQueries(valueOf("compare-query"));
    if (queries.length < 2 || queries.length > 6) {
      toast("对比需要 2–6 个标的", true);
      return;
    }
    const period = currentPeriodSelect($("compare-period"));
    const prefix = period === "日k" ? "" : `${period} `;
    void runCommand(`对比个股 ${prefix}${queries.join(" ")}`, "#compare-output");
  }

  function runTechnical() {
    const query = valueOf("technical-query") || state.query;
    if (!query) {
      toast("请输入标的", true);
      return;
    }
    const period = currentPeriodSelect($("technical-period"));
    const prefix = period === "日k" ? "" : `${period} `;
    void runCommand(`技术分析 ${prefix}${query}`, "#technical-output");
  }

  function runFinancial() {
    const query = valueOf("financial-query") || state.query;
    if (!query) {
      toast("请输入标的", true);
      return;
    }
    void runCommand(`财务 ${query}`, "#financial-output");
  }

  function runValuation() {
    const query = valueOf("valuation-query") || state.query;
    if (!query) {
      toast("请输入标的", true);
      return;
    }
    const kind = $("valuation-kind")?.value || "pe";
    void runCommand(`估值 ${kind} ${query}`, "#valuation-output");
  }

  function runReport() {
    const query = valueOf("report-query") || state.query;
    void runCommand(`股票报告${query ? ` ${query}` : ""}`, "#report-output");
  }

  async function runSector() {
    const kind = $("sector-kind").value;
    const falling = $("sector-order").value === "fall";
    try {
      const {data} = await api("/sector?" + new URLSearchParams({kind, falling: String(falling)}));
      const member = falling ? "领跌" : "领涨";
      const output = $("market-output");
      output.hidden = false;
      output.innerHTML = `<div class="table-wrap"><table class="data-table"><thead><tr>
        <th>板块</th><th>涨跌幅</th><th>${member}股</th><th>个股涨跌幅</th>
        </tr></thead><tbody>${boardRows(data.rows, falling, 30)}</tbody></table></div>`;
    } catch (e) {
      toast(String(e?.message || e), true);
    }
  }

  function runRank() {
    const value = $("rank-by")?.value || "main_inflow";
    void runCommand(`资金排行 ${value}`, "#rank-output");
  }

  function runNews() {
    void runCommand("股票资讯", "#news-output");
  }

  function addFromQuote() {
    const query = state.lastQuote?.code || state.query || valueOf("stock-query");
    if (!query) {
      toast("先查询一只标的", true);
      return;
    }
    void runCommand(`添加自选 ${query}`, "#stock-output");
  }

  function technicalFromQuote() {
    const query = state.lastQuote?.code || state.query || valueOf("stock-query");
    if (!query) {
      toast("先查询一只标的", true);
      return;
    }
    showView("research");
    setValue("technical-query", query);
    const chart = state.chart;
    if (chart && chart.type === "kline") setValue("technical-period", chart.period);
    runTechnical();
  }

  function reportFromQuote() {
    const query = state.lastQuote?.code || state.query || valueOf("stock-query");
    if (!query) {
      toast("先查询一只标的", true);
      return;
    }
    showView("research");
    setValue("report-query", query);
    runReport();
  }

  async function loadWatchlist() {
    const node = $("watch-list");
    if (!node) return;
    node.innerHTML = `<p class="hint">正在读取自选…</p>`;
    try {
      const payload = await api("/watchlist");
      const items = Array.isArray(payload?.items) ? payload.items : [];
      if (!items.length) {
        node.innerHTML = `<p class="hint">自选为空。可在右侧输入代码或名称添加，或使用命令「添加自选 600519」。</p>`;
        return;
      }
      node.innerHTML = items.map((item, index) => `<article class="watch-item">
        <div class="watch-main">
          <b>${esc(plain(item.name || item.query || item.code))}</b>
          <span>${esc(plain(item.code))}${item.query ? ` · ${esc(plain(item.query))}` : ""}</span>
        </div>
        <div class="watch-actions">
          <button class="ghost" type="button" data-watch-open="${esc(plain(item.code || item.query))}">查看行情</button>
          <button class="ghost danger-text" type="button" data-watch-remove="${esc(plain(item.code))}">删除</button>
        </div>
        <span class="watch-index">${index + 1}</span>
      </article>`).join("");
    } catch (error) {
      node.innerHTML = `<p class="hint">自选读取失败：${esc(error?.message || error)}</p>`;
    }
  }

  async function addWatch(raw) {
    const query = String(raw || "").trim();
    if (!query) {
      toast("请输入代码或名称", true);
      return false;
    }
    const payload = await runCommand(`添加自选 ${query}`, "#watch-output", { scroll: false });
    if (!payload) return false;
    await loadWatchlist();
    return true;
  }

  async function removeWatch(code) {
    if (!code) return false;
    const payload = await runCommand(`删除自选 ${code}`, "#watch-output", { scroll: false });
    if (!payload) return false;
    await loadWatchlist();
    return true;
  }

  async function clearWatch() {
    const payload = await runCommand("清空自选", "#watch-output", { scroll: false });
    if (!payload) return false;
    await loadWatchlist();
    return true;
  }

  async function myWatchCommand() {
    await runCommand("我的自选", "#watch-output", { scroll: false });
    await loadWatchlist();
    return true;
  }

  async function loadArtifacts() {
    const grid = $("artifact-grid");
    if (!grid) return;
    grid.innerHTML = `<p class="hint">正在读取制品…</p>`;
    try {
      const payload = await api("/artifacts");
      const items = Array.isArray(payload?.items) ? payload.items : [];
      if (!items.length) {
        grid.innerHTML = `<p class="hint">还没有制品。生成图表、快讯或研究报告后会出现在这里。</p>`;
        return;
      }
      grid.innerHTML = items.map((item) => {
        const isImage = String(item.mime || "").startsWith("image/");
        const key = registerPreview(artifactPreview(item));
        return `<article class="artifact-card">
          <div class="artifact-title">${esc(plain(item.title || item.id))}</div>
          <div class="artifact-meta">${esc(plain(item.kind))} · ${isImage ? "PNG" : "HTML"} · ${esc(fmtTime(item.created_at))}</div>
          <div class="artifact-meta">${esc(fmtBytes(item.size))}</div>
          <button class="ghost artifact-action" type="button" data-preview-key="${esc(key)}">页内预览</button>
        </article>`;
      }).join("");
    } catch (error) {
      grid.innerHTML = `<p class="hint">制品加载失败：${esc(error?.message || error)}</p>`;
    }
  }

  function refreshDiscover() {
    const active = document.querySelector(".subnav-item.is-active")?.dataset.discover || "screen";
    if (active === "rank") {
      runRank();
      return;
    }
    if (active === "news") {
      runNews();
      return;
    }
    const filled = ["screen-cap-min", "screen-cap-max", "screen-pe-max", "screen-change-min", "screen-change-max", "screen-industry"]
      .some((id) => valueOf(id));
    const command = filled ? screenCommand() : null;
    if (command) void runCommand(command, "#screen-output");
  }

  function refreshCurrentView() {
    if (state.view === "market") {
      state.overviewLoaded = false;
      void loadOverview();
    } else if (state.view === "discover") {
      refreshDiscover();
    } else if (state.view === "watchlist") {
      void loadWatchlist();
    } else if (state.view === "artifacts") {
      void loadArtifacts();
    } else if (state.view === "research" && valueOf("stock-query")) {
      void searchStock();
    }
    void loadHealth();
    toast("已刷新");
  }

  function fillPeriodSelects() {
    document.querySelectorAll("[data-period-select]").forEach((select) => {
      const current = select.dataset.default || "日k";
      select.replaceChildren(...PERIODS.map((item) => {
        const option = document.createElement("option");
        option.value = item.value;
        option.textContent = item.label;
        return option;
      }));
      select.value = PERIODS.some((item) => item.value === current) ? current : "日k";
    });
  }

  let features = [];
  let featureGroupsLoaded = false;

  async function loadFeatures() {
    if (features.length) return;
    try {
      features = (await api("/features")).items;
      $("feature-category").replaceChildren(...Array.from(new Set(features.map(f => f.category)), c => new Option(c, c)));
      chooseCategory();
    } catch (e) {
      $("feature-usage").textContent = String(e.message || e);
    }
  }

  async function loadFeatureGroups() {
    if (featureGroupsLoaded) return;
    try {
      const result = await api("/features/groups");
      $("feature-group").replaceChildren(new Option("个人", ""), ...result.items.map(g => new Option(g.name || g.id, g.id)));
      featureGroupsLoaded = true;
    } catch (e) {
      toast(String(e.message || e), true);
    }
  }

  function chooseCategory() {
    const list = features.filter(f => f.category === $("feature-category").value);
    $("feature-command").replaceChildren(...list.map(f => new Option(f.command, f.command)));
    chooseFeature();
  }

  function chooseFeature() {
    const f = features.find(f => f.command === $("feature-command").value);
    $("feature-usage").textContent = f?.usage || "";
    $("feature-submit").disabled = !f;
    $("feature-submit").textContent = f?.writes ? "办理" : "查询";
    $("feature-argument").value = "";
    $("feature-output").hidden = true;
  }

  async function submitFeature(e) {
    e.preventDefault();
    const spec = features.find(f => f.command === $("feature-command").value);
    if (!spec || $("feature-submit").disabled) return;
    const body = {argument: valueOf("feature-argument"), group_id: valueOf("feature-group")};
    $("feature-submit").disabled = true;
    try {
      if (spec.writes) {
        const dialog = $("feature-confirm");
        $("feature-confirm-detail").textContent = [spec.command, body.argument, $("feature-group").selectedOptions[0].text].filter(Boolean).join(" · ");
        dialog.returnValue = "";
        const closed = new Promise(resolve => dialog.addEventListener("close", resolve, {once: true}));
        dialog.showModal();
        await closed;
        if (dialog.returnValue !== "confirm") return;
        body.confirmed = true;
      }
      const result = await api("/features/" + encodeURIComponent(spec.command), {method: "POST", body: JSON.stringify(body)});
      renderCommand(result, spec.command, "#feature-output");
    } catch (error) {
      toast(String(error?.message || error), true);
    } finally {
      $("feature-submit").disabled = false;
    }
  }

  function bindEvents() {
    const boardTabs = [...document.querySelectorAll("[data-board-tab]")];
    boardTabs.forEach(tab => tab.addEventListener("click", () => {
      for (const other of boardTabs) {
        $(other.getAttribute("aria-controls")).hidden = other !== tab;
        other.setAttribute("aria-pressed", String(other === tab));
      }
    }));
    $("feature-category").addEventListener("change", chooseCategory);
    $("feature-command").addEventListener("change", chooseFeature);
    $("feature-group").addEventListener("focus", loadFeatureGroups);
    $("feature-form").addEventListener("submit", submitFeature);
    document.querySelectorAll("[data-view]").forEach((item) => {
      item.addEventListener("click", () => showView(item.dataset.view));
    });
    document.querySelectorAll("[data-discover]").forEach((item) => {
      item.addEventListener("click", () => showDiscover(item.dataset.discover));
    });
    document.querySelectorAll("[data-run-command]").forEach((item) => {
      item.addEventListener("click", () => {
        void runCommand(item.dataset.runCommand || "", item.dataset.output || "#help-output");
      });
    });
    document.querySelectorAll("[data-chart-preview]").forEach((item) => {
      item.addEventListener("click", () => {
        const [chartType, format] = String(item.dataset.chartPreview || "").split(":");
        if (chartType === "overview") {
          openChartPreview(format === "png" ? "png" : "html", {
            type: "overview",
            q: "上证指数",
            period: "日k",
            ndays: 1,
            title: "A股大盘概览",
          });
        } else {
          openChartPreview(format === "png" ? "png" : "html");
        }
      });
    });
    document.querySelectorAll("[data-output-close]").forEach((item) => {
      item.addEventListener("click", () => {
        const output = item.closest(".command-output");
        if (output) {
          output.hidden = true;
          output.innerHTML = "";
        }
      });
    });
    document.addEventListener("click", (event) => {
      const target = event.target instanceof Element ? event.target : null;
      if (!target) return;
      const preview = target.closest("[data-preview-key]");
      if (preview) {
        openPreview(preview.dataset.previewKey);
        return;
      }
      const closeOutput = target.closest("[data-output-close]");
      if (closeOutput) {
        const output = closeOutput.closest(".command-output");
        if (output) {
          output.hidden = true;
          output.innerHTML = "";
        }
        return;
      }
      const watchOpen = target.closest("[data-watch-open]");
      if (watchOpen) {
        const code = watchOpen.dataset.watchOpen || "";
        showView("research");
        setValue("stock-query", code);
        void searchStock();
        return;
      }
      const watchRemove = target.closest("[data-watch-remove]");
      if (watchRemove) {
        void removeWatch(watchRemove.dataset.watchRemove || "");
        return;
      }
      const quick = target.closest("[data-quick-query]");
      if (quick) {
        setValue("stock-query", quick.dataset.quickQuery || "");
        void searchStock();
      }
    });

    $("refresh-btn")?.addEventListener("click", refreshCurrentView);
    $("overview-command")?.addEventListener("click", () => {
      void runCommand("大盘概览", "#market-output");
    });
    $("sector-form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      runSector();
    });
    $("screen-form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      const command = screenCommand();
      if (command) void runCommand(command, "#screen-output");
    });
    document.querySelectorAll("[data-screen-preset]").forEach((item) => {
      item.addEventListener("click", () => presetScreen(item.dataset.screenPreset));
    });
    $("rank-form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      runRank();
    });
    $("news-refresh")?.addEventListener("click", runNews);
    $("stock-mode")?.addEventListener("change", syncStockMode);
    syncStockMode();
    $("stock-form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      void searchStock();
    });
    $("stock-query")?.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        void searchStock();
      }
    });
    $("quote-add-watch")?.addEventListener("click", addFromQuote);
    $("quote-technical")?.addEventListener("click", technicalFromQuote);
    $("quote-report")?.addEventListener("click", reportFromQuote);
    $("quote-command")?.addEventListener("click", runStockCommand);
    $("chart-html")?.addEventListener("click", () => openChartPreview("html"));
    $("chart-png")?.addEventListener("click", () => openChartPreview("png"));
    $("technical-form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      runTechnical();
    });
    $("compare-form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      runCompare();
    });
    $("financial-form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      runFinancial();
    });
    $("valuation-form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      runValuation();
    });
    $("report-form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      runReport();
    });
    $("watch-add-form")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (await addWatch(valueOf("watch-add-query"))) setValue("watch-add-query", "");
    });
    $("watch-clear")?.addEventListener("click", () => {
      void clearWatch();
    });
    $("watch-mine")?.addEventListener("click", () => {
      void myWatchCommand();
    });
    $("artifacts-refresh")?.addEventListener("click", () => {
      void loadArtifacts();
    });
    $("command-form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      const command = valueOf("command-input");
      if (!command) {
        toast("请输入命令", true);
        return;
      }
      void runCommand(command, "#help-output").then((payload) => {
        if (payload) setValue("command-input", "");
      });
    });
  }

  function boot() {
    if (window.self !== window.top || new URLSearchParams(location.search).get("embed") === "1") {
      document.documentElement.classList.add("qweb-embed");
    }
    let paletteTimer;
    new MutationObserver(() => {
      clearTimeout(paletteTimer);
      paletteTimer = setTimeout(() => {
        for (const frame of document.querySelectorAll("#chart-frame[src], .preview-frame[src]")) {
          const url = new URL(frame.src, location.href);
          if (url.origin !== location.origin || !url.pathname.endsWith("/api/chart/html")) continue;
          url.searchParams.set("colors", chartColors());
          url.searchParams.set("mode", ["light","sakura"].includes(document.documentElement.dataset.theme) ? "light" : "dark");
          if (url.href !== frame.src) frame.src = url.href;
        }
      }, 120);
    }).observe(document.documentElement, {attributes:true, attributeFilter:["style","data-theme"]});
    fillPeriodSelects();
    setupPreviewDialog();
    bindEvents();
    const initial = String(location.hash || "").replace(/^#/, "");
    showView(VIEW_META[initial] ? initial : "market");
    void loadHealth();

  }

  boot();
})();