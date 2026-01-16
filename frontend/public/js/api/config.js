import { state } from "../state/state.js";
import { el } from "../util/dom.js";
import { fetchWithTimeout } from "../api/fetch.js";
import { setConfigBadge, setConn } from "../ui/connection.js";
import { showGlobalMsg } from "../ui/globalMsg.js";

export async function loadConfig() {
  try {
    // config.json は軽いので短めでOK（10秒）
    const resp = await fetchWithTimeout("/config.json", { method: "GET" }, 10000);
    if (!resp.ok) throw new Error("config.json not found");
    const cfg = await resp.json();

    if (!cfg.apiEndpoint || typeof cfg.apiEndpoint !== "string") {
      throw new Error("config.json: apiEndpoint is required");
    }

    state.config = cfg;
    state.apiEndpoint = cfg.apiEndpoint.replace(/\/$/, "");

    // configで指定があれば反映（なければ 45000 のまま）
    const v = Number(cfg.requestTimeoutMs);
    if (Number.isFinite(v) && v >= 5000) state.requestTimeoutMs = v;

    const cat = el("category");
    const lvl = el("level");
    if (cfg.defaultCategory && cat) cat.value = cfg.defaultCategory;
    if (cfg.defaultLevel && lvl) lvl.value = String(cfg.defaultLevel);

    setConfigBadge(true, `API: ${state.apiEndpoint}`);
    setConn(true, "設定OK");
  } catch (e) {
    console.error("[config] load failed", e);
    setConfigBadge(false, "設定読み込み失敗（/config.json）");
    setConn(false, "未接続");
    showGlobalMsg("config.json の読み込みに失敗しました。S3に /config.json が配置されているか確認してください。");
  }
}
