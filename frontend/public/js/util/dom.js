export const el = (id) => document.getElementById(id);

export function toggleHidden(node, hidden) {
  if (!node) return;
  node.classList.toggle("hidden", !!hidden);
}

export function setText(node, text) {
  if (!node) return;
  node.textContent = text ?? "";
}

export function escapeHtml(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
