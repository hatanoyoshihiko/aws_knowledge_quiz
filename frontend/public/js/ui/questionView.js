import { el } from "../util/dom.js";
import { state } from "../state/state.js";
import { resetResult } from "./resultView.js";

export function setQuestionView(q) {
  state.currentQuestion = q;
  state.questionId = q?.questionId ?? null;

  el("qTitle").textContent = q?.title || "—";
  el("qBody").textContent = q?.body || "—";
  el("qMeta").textContent = q?.createdAt ? `出題: ${q.createdAt}` : "—";
  el("qId").textContent = q?.questionId ? q.questionId : "—";

  const catBadge = el("qCategoryBadge");
  const lvlBadge = el("qLevelBadge");

  if (q?.category) {
    catBadge.textContent = `カテゴリ: ${q.category}`;
    catBadge.classList.remove("hidden");
  } else catBadge.classList.add("hidden");

  if (q?.level) {
    lvlBadge.textContent = `レベル: ${q.level}`;
    lvlBadge.classList.remove("hidden");
  } else lvlBadge.classList.add("hidden");

  const ans = el("answer");
  if (ans) ans.value = "";

  // result/submit はチーム画面にのみ存在する
  try { resetResult(); } catch (_) {}
  const submit = el("submitBtn");
  if (submit) submit.disabled = !state.questionId;
}

export function resetAll({ clearGlobalMsg, toast }) {
  state.questionId = null;
  state.currentQuestion = null;
  el("qTitle").textContent = "次のクイズを取得してください";
  el("qBody").textContent = "サイドバーまたは上部の「次のクイズ」を押すと問題が表示されます。";
  el("qMeta").textContent = "—";
  el("qId").textContent = "—";
  el("qCategoryBadge").classList.add("hidden");
  el("qLevelBadge").classList.add("hidden");
  const ans = el("answer");
  if (ans) ans.value = "";
  try { resetResult(); } catch (_) {}
  clearGlobalMsg?.();
  toast?.("画面をリセットしました");
  const submit = el("submitBtn");
  if (submit) submit.disabled = true;
}
