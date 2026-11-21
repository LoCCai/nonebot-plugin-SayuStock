/* QWeb host bridge for the SayuStock page.
 *
 * The page can be embedded by a QWeb admin shell and driven either by the
 * normal UI or by postMessage.  The protocol is intentionally small:
 *
 *   parent -> iframe : { source: "qweb", type: "sayustock.chat", message }
 *                      { source: "qweb", type: "sayustock.command", text }
 *                      { source: "qweb", type: "sayustock.open", path }
 *   iframe -> parent : { source: "sayustock", type: "sayustock.ready" }
 *                      { source: "sayustock", type: "sayustock.reply", ... }
 *                      { source: "sayustock", type: "sayustock.error", message }
 */
(() => {
  "use strict";

  const API = "/sayustock/api";
  const token = () => localStorage.getItem("sayustock_token") || "";

  async function request(path, options = {}) {
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    const value = token();
    if (value) headers.Authorization = "Bearer " + value;
    const response = await fetch(API + path, { ...options, headers });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || payload.message || `HTTP ${response.status}`);
    return payload;
  }

  function post(type, payload = {}) {
    if (window.parent === window) return;
    window.parent.postMessage({ source: "sayustock", type, ...payload }, "*");
  }

  async function chat(message, history = []) {
    try {
      const result = await request("/chat", {
        method: "POST",
        body: JSON.stringify({ message, history }),
      });
      post("sayustock.reply", { result });
      return result;
    } catch (error) {
      post("sayustock.error", { message: String(error.message || error) });
      throw error;
    }
  }

  async function command(text) {
    try {
      const result = await request("/command", {
        method: "POST",
        body: JSON.stringify({ text }),
      });
      post("sayustock.reply", { result });
      return result;
    } catch (error) {
      post("sayustock.error", { message: String(error.message || error) });
      throw error;
    }
  }

  window.SayuStockQWeb = {
    version: "0.9.0",
    mount: "/sayustock",
    chat,
    command,
    openArtifact(id) {
      post("sayustock.open", { path: `/sayustock/artifact/${id}.html` });
    },
  };

  window.addEventListener("message", (event) => {
    const data = event.data;
    if (!data || data.source !== "qweb") return;
    if (data.type === "sayustock.chat" && typeof data.message === "string") {
      void chat(data.message, Array.isArray(data.history) ? data.history : []);
    } else if (data.type === "sayustock.command" && typeof data.text === "string") {
      void command(data.text);
    } else if (data.type === "sayustock.open" && typeof data.path === "string") {
      if (data.path.startsWith("/sayustock/")) window.location.href = data.path;
    }
  });

  const embedded = new URLSearchParams(window.location.search).get("embed") === "1";
  if (embedded) document.documentElement.classList.add("qweb-embed");
  post("sayustock.ready", { mount: "/sayustock", embedded });
})();