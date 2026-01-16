import { el } from "../util/dom.js";

export function showGlobalMsg(msg) {
  const box = el("globalMsg");
  if (!box) return;
  box.textContent = msg ?? "";
  box.classList.remove("hidden");
}

export function clearGlobalMsg() {
  const box = el("globalMsg");
  if (!box) return;
  box.classList.add("hidden");
  box.textContent = "";
}
