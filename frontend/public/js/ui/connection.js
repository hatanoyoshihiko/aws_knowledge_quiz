import { el } from "../util/dom.js";
import { state } from "../state/state.js";

export function setConfigBadge(ok, text) {
  const b = el("configBadge");
  if (!b) return;
  b.classList.remove("hidden");
  b.textContent = text ?? "";
  b.className = ok
    ? "hidden sm:inline-flex items-center rounded-full bg-emerald-500/15 px-3 py-1 text-xs font-medium text-emerald-200 ring-1 ring-emerald-500/30"
    : "hidden sm:inline-flex items-center rounded-full bg-amber-500/15 px-3 py-1 text-xs font-medium text-amber-200 ring-1 ring-amber-500/30";
}

export function setConn(ok, text) {
  const dot = el("connDot");
  const connText = el("connText");
  const apiText = el("apiText");
  const timeoutText = el("timeoutText");

  if (dot) {
    dot.className = ok
      ? "inline-block h-2.5 w-2.5 rounded-full bg-emerald-400"
      : "inline-block h-2.5 w-2.5 rounded-full bg-slate-500";
  }
  if (connText) connText.textContent = text ?? "";
  if (apiText) apiText.textContent = `apiEndpoint: ${state.apiEndpoint || "-"}`;
  if (timeoutText) timeoutText.textContent = `timeoutMs: ${state.requestTimeoutMs || "-"}`;
}
