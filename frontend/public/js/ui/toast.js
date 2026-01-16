import { el } from "../util/dom.js";

export function toast(msg) {
  const t = el("toast");
  if (!t) return;
  t.textContent = msg ?? "";
  t.classList.remove("hidden");
  setTimeout(() => t.classList.add("hidden"), 2400);
}
