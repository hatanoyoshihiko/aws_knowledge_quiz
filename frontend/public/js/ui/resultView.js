import { el, escapeHtml } from "../util/dom.js";

export function resetResult() {
  el("resultBadge").classList.add("hidden");
  el("scoreText").textContent = "";
  el("feedback").textContent = "ここに採点結果が表示されます。";
  el("pointsWrap").classList.add("hidden");

  // 折りたたみ状態も初期化
  const body = el("pointsBody");
  const icon = el("pointsToggleIcon");
  const text = el("pointsToggleText");
  if (body && icon && text) {
    body.classList.add("hidden"); // デフォルトで隠す
    icon.textContent = "▼";
    text.textContent = "表示";
  }

  el("metPoints").innerHTML = "";
  el("missingPoints").innerHTML = "";
  el("hintWrap").classList.add("hidden");
  el("hintWrap").textContent = "";
}

function normalizeP(p) {
  return String(p || "").toUpperCase();
}

function formatDetail(x) {
  const p = normalizeP(x?.p);
  const text = (x?.label || x?.description || "").trim();
  return text ? `${p}: ${text}` : p;
}

function toScore100(score01) {
  // score01: 0.0〜1.0 を想定（念のため clamp）
  const s = Number(score01);
  if (!Number.isFinite(s)) return null;
  const clamped = Math.min(1, Math.max(0, s));
  return Math.round(clamped * 100);
}

export function setResult(result) {
  const badge = el("resultBadge");
  badge.classList.remove("hidden");

  const r = result.result;
  if (r === "correct") {
    badge.textContent = "正解";
    badge.className = "rounded-full bg-emerald-500/15 px-2.5 py-1 text-base font-semibold text-emerald-200 ring-1 ring-emerald-500/30";
  } else if (r === "close") {
    badge.textContent = "おしい";
    badge.className = "rounded-full bg-amber-500/15 px-2.5 py-1 text-sm font-semibold text-amber-200 ring-1 ring-amber-500/30";
  } else {
    badge.textContent = "不正解";
    badge.className = "rounded-full bg-rose-500/15 px-2.5 py-1 text-xs font-semibold text-rose-200 ring-1 ring-rose-500/30";
  }

  // ★ここを100点満点の整数表示に変更
const score100 = toScore100(result.score);

const scoreEl = el("scoreText");
scoreEl.textContent = (score100 === null) ? "" : `score: ${score100} / 100`;

// 見た目を強制（他ファイル/旧CSSの上書きに勝つ）
scoreEl.className = "text-2xl font-bold text-slate-100";

  el("feedback").textContent = result.feedback || "";

  const met = Array.isArray(result.mustPointsMetDetails) && result.mustPointsMetDetails.length
    ? result.mustPointsMetDetails.map(formatDetail)
    : (Array.isArray(result.mustPointsMet) ? result.mustPointsMet.map(normalizeP) : []);

  const missing = Array.isArray(result.missingMustPointsDetails) && result.missingMustPointsDetails.length
    ? result.missingMustPointsDetails.map(formatDetail)
    : (Array.isArray(result.missingMustPoints) ? result.missingMustPoints.map(normalizeP) : []);

  const hint = (result.nextHint && String(result.nextHint).trim().length > 0) ? String(result.nextHint).trim() : "";

  if (met.length || missing.length || hint) {
    el("pointsWrap").classList.remove("hidden");

    // 採点直後は閉じた状態（ボタンだけ見える）
    const body = el("pointsBody");
    const icon = el("pointsToggleIcon");
    const text = el("pointsToggleText");
    if (body && icon && text) {
      body.classList.add("hidden");
      icon.textContent = "▼";
      text.textContent = "表示";
    }

    el("metPoints").innerHTML = met.map(x => `<li>${escapeHtml(x)}</li>`).join("");
    el("missingPoints").innerHTML = missing.map(x => `<li>${escapeHtml(x)}</li>`).join("");

    const hintWrap = el("hintWrap");
    if (hint) {
      hintWrap.classList.remove("hidden");
      hintWrap.textContent = `ヒント: ${hint}`;
    } else {
      hintWrap.classList.add("hidden");
      hintWrap.textContent = "";
    }
  } else {
    el("pointsWrap").classList.add("hidden");
  }
}

export function wirePointsToggle() {
  el("pointsToggleBtn")?.addEventListener("click", () => {
    const body = el("pointsBody");
    const icon = el("pointsToggleIcon");
    const text = el("pointsToggleText");
    if (!body || !icon || !text) return;

    const willOpen = body.classList.contains("hidden");
    body.classList.toggle("hidden", !willOpen);

    icon.textContent = willOpen ? "▲" : "▼";
    text.textContent = willOpen ? "非表示" : "表示";
  });
}
