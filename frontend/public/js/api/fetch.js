import { state } from "../state/state.js";

export function isAbortError(e) {
  return e && (e.name === "AbortError" || String(e.message || "").toLowerCase().includes("timeout"));
}

// 原因究明ログつき fetch
export async function fetchWithTimeout(url, options = {}, timeoutMsOverride = null) {
  const timeoutMs = Number(timeoutMsOverride ?? state.requestTimeoutMs ?? 45000);
  const controller = new AbortController();
  const t = setTimeout(() => {
    try { controller.abort(new Error("timeout")); }
    catch (_) { controller.abort(); }
  }, timeoutMs);

  const t0 = performance.now();
  console.log("[fetch] start", { url, timeoutMs, method: options?.method || "GET" });

  try {
    const resp = await fetch(url, { ...options, signal: controller.signal, cache: "no-store" });
    console.log("[fetch] response", { url, status: resp.status, ok: resp.ok, ms: Math.round(performance.now() - t0) });
    return resp;
  } catch (e) {
    console.error("[fetch] failed", { url, ms: Math.round(performance.now() - t0), error: e });
    throw e;
  } finally {
    clearTimeout(t);
  }
}
