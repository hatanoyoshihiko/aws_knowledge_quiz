import { state } from "./state/state.js";

import { loadConfig } from "./api/config.js";
// ★ named export 前提をやめる（currentQuiz が無くても落ちない）
import * as quizApi from "./api/quizApi.team.js";

import { el } from "./util/dom.js";
import { setLoading } from "./ui/loading.js";
import { setConn } from "./ui/connection.js";
import { toast } from "./ui/toast.js";
import { clearGlobalMsg } from "./ui/globalMsg.js";
import { resetAll } from "./ui/questionView.js";
import { resetResult, wirePointsToggle } from "./ui/resultView.js";
import { setupSpeech, toggleMic, stopMic } from "./ui/speech.js";
import { copyQid } from "./ui/clipboard.js";
import { toggleSidebar } from "./ui/sidebar.js";
import { setupShortcuts } from "./ui/shortcuts.js";
import { loadHistory, clearHistory } from "./state/history.js";

// ✅ タブ単位でチーム名を保持（localStorageだとタブ間で上書きされる）
const TEAM_NAME_STORAGE = "awsQuizTeamName_session";
const POLL_MS = 30000; //30秒

function _normalizeTeamName(name) {
  const n = String(name || "").trim();
  return n || "Team";
}

// ✅ チーム名から安定した teamId を作る（同一ブラウザでも Team A / Team B を分離）
function _teamIdFromName(teamName) {
  const n = _normalizeTeamName(teamName)
    .toLowerCase()
    .replace(/\s+/g, "-")
    .replace(/[^a-z0-9\-_]/g, "");
  return `team:${n || "team"}`;
}

function _restoreTeamName() {
  const n = el("teamName");
  if (!n) return;

  try {
    n.value = sessionStorage.getItem(TEAM_NAME_STORAGE) || "";
  } catch (_) {}

  n.addEventListener("input", () => {
    try {
      sessionStorage.setItem(TEAM_NAME_STORAGE, (n.value || "").trim());
    } catch (_) {}
  });
}

// ------------------------
// quizApi の関数解決（export差分に強くする）
// ------------------------
function _getCurrentQuizFn() {
  // currentQuiz が無い環境があるためフォールバック
  if (typeof quizApi.currentQuiz === "function") return quizApi.currentQuiz;
  if (typeof quizApi.nextQuiz === "function") {
    // nextQuiz は引数を取らないのでラップ
    return async ({ silent = false } = {}) => {
      if (!silent) {
        // nextQuiz は内部で toast など出す可能性があるが許容
      }
      return quizApi.nextQuiz();
    };
  }
  return null;
}

function _getSubmitAnswerFn() {
  if (typeof quizApi.submitAnswer === "function") return quizApi.submitAnswer;
  return null;
}

async function _poll() {
  const fn = _getCurrentQuizFn();
  if (!fn) return;

  try {
    await fn({ silent: true });
  } catch (_) {
    // polling では失敗しても無視
  }
}

// ------------------------
// Config fallback (robust)
// 以前の「相対パスで取れる」挙動を維持する
// ------------------------
async function ensureApiEndpoint() {
  if (state.apiEndpoint && String(state.apiEndpoint).trim() !== "") return;

  const candidates = [
    new URL("config.json", window.location.href).toString(),
    new URL("./config.json", window.location.href).toString(),
    new URL("../config.json", window.location.href).toString(),
    new URL("/config.json", window.location.origin).toString(),
  ];

  for (const url of candidates) {
    try {
      const res = await fetch(url, { cache: "no-store" });
      if (!res.ok) continue;
      const cfg = await res.json();
      if (cfg?.apiEndpoint && String(cfg.apiEndpoint).trim() !== "") {
        state.apiEndpoint = cfg.apiEndpoint;
        return;
      }
    } catch (_) {}
  }

  state.apiEndpoint = ".";
}

// ------------------------
// Score submit
// ------------------------
async function submitScoreDelta({ apiBase, teamId, teamName, delta }) {
  const url = `${apiBase}/scores/submit`;
  const payload = { teamId, teamName, delta };

  console.log("[scores/submit] request", url, payload);

  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch (_) {
    data = null;
  }

  if (!res.ok) {
    const msg = `[scores/submit] HTTP ${res.status} ${res.statusText} ${(text || "").slice(0, 200)}`;
    console.warn(msg);
    throw new Error(msg);
  }

  console.log("[scores/submit] response", data ?? text ?? "(no body)");
  return data ?? { ok: true };
}

// ------------------------
// Score parsing (from UI)
// ------------------------
function _getScore100FromScoreText() {
  const raw = (el("scoreText")?.textContent || "").trim();
  // 例: "score: 20 / 100"
  const m = raw.match(/score:\s*(\d{1,3})/i);
  if (!m) return null;

  const n = Number(m[1]);
  if (!Number.isFinite(n)) return null;

  return Math.max(0, Math.min(100, Math.round(n)));
}

// ------------------------
// 二重送信・二重加算防止
// ------------------------
function _getCurrentQidFromDom() {
  const ids = ["qid", "qidText", "questionId", "quizId", "currentQid"];
  for (const id of ids) {
    const node = el(id);
    const t = node?.textContent ? String(node.textContent).trim() : "";
    if (t) return t;
  }
  return null;
}

let _submitInFlight = false;
let _lastScoredKey = null; // `${teamId}:${qid}`

// ------------------------
// Main action: judge -> read scoreText -> submit score
// ------------------------
async function submitAnswerAndScore() {
  const apiBase = state.apiEndpoint;
  if (!apiBase || String(apiBase).trim() === "") {
    toast("API未接続（config.json）");
    return;
  }

  const submitFn = _getSubmitAnswerFn();
  if (!submitFn) {
    toast("submitAnswer が見つかりません（quizApi.js の export を確認してください）");
    console.warn("[mainTeam] submitAnswer export not found", quizApi);
    return;
  }

  const teamName = _normalizeTeamName(el("teamName")?.value);
  const teamId = _teamIdFromName(teamName);

  const qid = _getCurrentQidFromDom();
  const scoreKey = `${teamId}:${qid || "unknown"}`;
  if (_lastScoredKey === scoreKey) {
    toast("同じ問題は二重に加算しません（次のクイズに進んでください）");
    return;
  }

  if (_submitInFlight) return;
  _submitInFlight = true;

  try {
    // ① 採点（戻り値は信用しない）
    try {
      await submitFn();
    } catch (e) {
      console.warn("[score] submitAnswer failed", e);
      toast("採点に失敗しました（Console参照）");
      return;
    }

    // ② scoreText から 0〜100 を取得して送る
    const delta = _getScore100FromScoreText();
    if (delta == null || delta <= 0) {
      console.warn("[score] could not parse scoreText or delta<=0, skip submit", {
        scoreText: el("scoreText")?.textContent || "",
        delta,
      });
      toast("0点のためスコアは加算されません");
      return;
    }

    try {
      await submitScoreDelta({ apiBase, teamId, teamName, delta });
      _lastScoredKey = scoreKey;
      toast(`スコア送信: ${teamName} +${delta}pts`);
    } catch (e) {
      console.warn("[score] submit failed", e);
      toast(`スコア送信失敗: ${String(e.message || e).slice(0, 140)}`);
    }
  } finally {
    _submitInFlight = false;
  }
}

// ------------------------
// Boot
// ------------------------
window.addEventListener("DOMContentLoaded", async () => {
  setLoading(true);
  try {
    await loadConfig();
  } finally {
    await ensureApiEndpoint();
    setLoading(false);
  }

  _restoreTeamName();
  setupSpeech();
  loadHistory();
  wirePointsToggle();

  setupShortcuts({
    submitAnswer: submitAnswerAndScore,
    stopMic,
    toast,
    focusAnswer: () => el("answer")?.focus(),
  });

  el("submitBtn")?.addEventListener("click", submitAnswerAndScore);
  el("micBtn")?.addEventListener("click", toggleMic);

  const refreshFn = _getCurrentQuizFn();
  const refresh = () => (refreshFn ? refreshFn({ silent: false }) : toast("quiz取得関数が見つかりません"));

  el("refreshBtn")?.addEventListener("click", refresh);
  el("refreshBtnSide")?.addEventListener("click", refresh);

  el("clearBtn")?.addEventListener("click", () => {
    const a = el("answer");
    if (a) a.value = "";
    resetResult();
    clearGlobalMsg();
    toast("回答をクリアしました");
  });

  el("focusAnswerBtn")?.addEventListener("click", () => {
    el("answer")?.focus();
    toast("回答欄へフォーカス");
  });

  el("resetBtn")?.addEventListener("click", () => resetAll({ clearGlobalMsg, toast }));
  el("copyQidBtn")?.addEventListener("click", copyQid);
  el("sidebarToggle")?.addEventListener("click", toggleSidebar);
  el("clearHistoryBtn")?.addEventListener("click", clearHistory);

  setConn(!!state.apiEndpoint, state.apiEndpoint ? "設定OK" : "未接続");

  // 初回同期
  if (refreshFn) {
    await refreshFn({ silent: true });
    setInterval(_poll, POLL_MS);
  } else {
    console.warn("[mainTeam] currentQuiz/nextQuiz export not found", quizApi);
  }
});
