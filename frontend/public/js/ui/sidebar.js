import { el } from "../util/dom.js";

export function toggleSidebar() {
  const sb = el("sidebar");
  if (!sb) return;
  sb.classList.toggle("hidden");
}
