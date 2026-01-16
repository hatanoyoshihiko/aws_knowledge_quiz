import { el, escapeHtml } from "../util/dom.js";
import { state } from "../state/state.js";
import { toast } from "../ui/toast.js";

const HISTORY_KEY = "aws_quiz_history_v1";
const HISTORY_MAX = 10;

export function loadHistory() {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    state.history = Array.isArray(arr) ? arr : [];
  } catch (_) {
    state.history = [];
  }
  renderHistory();
}

function saveHistory() {
  localStorage.setItem(HISTORY_KEY, JSON.stringify(state.history.slice(0, HISTORY_MAX)));
}

export function addHistory(entry) {
  state.history = [entry, ...state.history.filter(x => x.questionId !== entry.questionId)];
  state.history = state.history.slice(0, HISTORY_MAX);
  saveHistory();
  renderHistory();
}

export function clearHistory() {
  state.history = [];
  saveHistory();
  renderHistory();
  toast("履歴をクリアしました");
}

export function renderHistory() {
  const wrap = el("historyList");
  if (!wrap) return;
  wrap.innerHTML = "";

  if (!state.history.length) {
    wrap.innerHTML = `
      <div class="rounded-2xl border border-slate-800 bg-slate-950 p-3 text-xs text-slate-400">
        まだ履歴がありません。
      </div>
    `;
    return;
  }

  for (const h of state.history) {
    const r = h.result || "—";
    const pillClass =
      r === "correct" ? "bg-emerald-500/15 text-emerald-200 ring-emerald-500/30" :
      r === "close" ? "bg-amber-500/15 text-amber-200 ring-amber-500/30" :
      r === "incorrect" ? "bg-rose-500/15 text-rose-200 ring-rose-500/30" :
      "bg-slate-800 text-slate-200 ring-slate-700";

    const btn = document.createElement("button");
    btn.className = "w-full text-left rounded-2xl border border-slate-800 bg-slate-950 p-3 hover:bg-slate-900 focus:outline-none focus:ring-2 focus:ring-emerald-300";
    btn.innerHTML = `
      <div class="flex items-start justify-between gap-2">
        <div class="min-w-0">
          <div class="truncate text-sm font-semibold text-slate-100">${escapeHtml(h.title || "(no title)")}</div>
          <div class="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
            <span>${escapeHtml(h.category || "")}</span>
            <span class="text-slate-600">•</span>
            <span>Lv ${escapeHtml(h.level || "")}</span>
            <span class="text-slate-600">•</span>
            <span class="truncate">${escapeHtml(h.at || "")}</span>
          </div>
        </div>
        <span class="shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ${pillClass}">${escapeHtml(r)}</span>
      </div>
    `;
    btn.addEventListener("click", () => {
      if (h.questionId) {
        state.questionId = h.questionId;
        el("qId").textContent = h.questionId;
        toast("復習用に questionId をセットしました");
        el("answer").focus();
        el("submitBtn").disabled = false;
      }
    });
    wrap.appendChild(btn);
  }
}
