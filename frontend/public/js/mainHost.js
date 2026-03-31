import { state } from "./state/state.js";

import { loadConfig } from "./api/config.js";
// ★ named export 前提をやめる（getHostKey が無くても落ちない）
import * as quizApi from "./api/quizApi.host.js";

import { el } from "./util/dom.js";
import { setLoading } from "./ui/loading.js";
import { setConn } from "./ui/connection.js";
import { toast } from "./ui/toast.js";
import { clearGlobalMsg } from "./ui/globalMsg.js";
import { resetAll } from "./ui/questionView.js";
import { copyQid } from "./ui/clipboard.js";
import { toggleSidebar } from "./ui/sidebar.js";
import { setupShortcuts } from "./ui/shortcuts.js";

const HOST_KEY_STORAGE = "awsQuizHostKey";

// ========================
// Scoreboard 表示設定
// ========================
const SCOREBOARD_MAX_RANK = 10;      // 通常表示は上位10位まで
const SCOREBOARD_REST_MAX_HEIGHT_PX = 260; // 11位以降のスクロール領域高さ

// ------------------------
// HostKey helpers (non-breaking)
// quizApi.js に setHostKey/getHostKey があればそれを使い、無ければここで補完
// ------------------------
function setHostKeySafe(key) {
  const v = String(key ?? "").trim();

  if (typeof quizApi.setHostKey === "function") {
    quizApi.setHostKey(v);
    return;
  }
  // フォールバック（mainHost.js内だけ）
  try {
    sessionStorage.setItem(HOST_KEY_STORAGE, v);
  } catch (_) {}
}

function getHostKeySafe() {
  if (typeof quizApi.getHostKey === "function") {
    return quizApi.getHostKey();
  }
  try {
    return (sessionStorage.getItem(HOST_KEY_STORAGE) || "").trim();
  } catch (_) {
    return "";
  }
}

// ------------------------
// Config fallback (safe)
// loadConfig が反映されないときだけ補完（非破壊）
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
}

function joinUrl(base, path) {
  const b = String(base || "").replace(/\/+$/, "");
  const p = String(path || "").replace(/^\/+/, "");
  return `${b}/${p}`;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

function rankBadge(i) {
  if (i === 0) return "🥇";
  if (i === 1) return "🥈";
  if (i === 2) return "🥉";
  return `#${i + 1}`;
}

function makeScoreRow(teamObj, rankIndex) {
  const t = teamObj || {};

  // ✅ 表示は常に teamName（or name）を優先し、teamId は表示しない
  const displayNameRaw = (t.teamName ?? t.name ?? "").toString().trim();
  const team = escapeHtml(displayNameRaw || `Team ${rankIndex + 1}`);

  const score = Number(t.score ?? t.pts ?? 0) || 0;

  const row = document.createElement("div");
  row.className =
    "flex items-center justify-between gap-3 rounded-xl border border-slate-800 bg-slate-900/60 px-4 py-3";

  // 内部キーとして teamId は保持（必要ならデバッグ/将来用途に使える）
  row.dataset.teamId = (t.teamId ?? "").toString();

  row.innerHTML = `
    <div class="flex items-center gap-3">
      <div class="text-lg">${rankBadge(rankIndex)}</div>
      <div class="min-w-0">
        <div class="truncate font-semibold text-slate-100">${team}</div>
      </div>
    </div>
    <div class="text-right">
      <div class="text-xl font-bold text-slate-100">${score}</div>
      <div class="text-xs text-slate-400">pts</div>
    </div>
  `;

  return row;
}

/**
 * Scoreboard 描画:
 * - 上位10位までは通常表示
 * - 11位以降は <details> で折りたたみ。中身はスクロール領域
 */
function renderScoreboard(items) {
  const root = el("scoreboardList");
  if (!root) return;

  root.innerHTML = "";

  const sorted = [...(items || [])].sort((a, b) => Number(b.score || 0) - Number(a.score || 0));
  const top = sorted.slice(0, SCOREBOARD_MAX_RANK);
  const rest = sorted.slice(SCOREBOARD_MAX_RANK);

  // ---- top (1〜10位)
  for (let i = 0; i < top.length; i++) {
    root.appendChild(makeScoreRow(top[i], i));
  }

  // ---- rest (11位以降) は折りたたみ + スクロール
  if (rest.length > 0) {
    const details = document.createElement("details");
    details.className =
      "mt-2 rounded-2xl border border-slate-800 bg-slate-900/30 p-3";

    // デフォルトは閉じておく（必要なら details.open = true; で開ける）
    // details.open = false;

    const summary = document.createElement("summary");
    summary.className =
      "cursor-pointer select-none list-none rounded-xl border border-slate-800 bg-slate-950 px-3 py-2 text-xs font-semibold text-slate-200 hover:bg-slate-900";
    summary.textContent = `11位以降を表示（${rest.length}チーム）`;

    // summary のデフォルトマーカーを消す（ブラウザ差異があるので補助）
    // Tailwindだけでは消えないことがあるため、微調整
    summary.style.outline = "none";

    const box = document.createElement("div");
    box.className = "mt-3 space-y-2";
    box.style.maxHeight = `${SCOREBOARD_REST_MAX_HEIGHT_PX}px`;
    box.style.overflow = "auto";
    box.style.paddingRight = "4px"; // スクロールバーで文字が隠れにくい

    for (let i = 0; i < rest.length; i++) {
      const rankIndex = SCOREBOARD_MAX_RANK + i; // 10位の次=11位
      box.appendChild(makeScoreRow(rest[i], rankIndex));
    }

    details.appendChild(summary);
    details.appendChild(box);
    root.appendChild(details);
  }
}

async function fetchScores() {
  if (!state.apiEndpoint) throw new Error("apiEndpoint が未設定です（config.json を確認）");
  const url = joinUrl(state.apiEndpoint, "/scores");

  const res = await fetch(url, { method: "GET" });
  const text = await res.text();

  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch (_) {
    data = null;
  }

  if (!res.ok) throw new Error((data && data.message) ? data.message : `scores fetch failed (${res.status})`);
  if (data && typeof data === "object" && data.ok === false) {
    throw new Error(data?.message || "scores fetch failed (ok=false)");
  }

  if (Array.isArray(data)) return data;
  return data?.items || data?.scores || [];
}

function setupScoreboard() {
  const meta = el("scoreboardMeta");
  const refreshBtn = el("scoreboardRefreshBtn");
  const auto = el("scoreboardAuto");

  let timer = null;

  const refresh = async () => {
    try {
      if (meta) meta.textContent = "読み込み中…";
      const items = await fetchScores();
      renderScoreboard(items);

      const now = new Date().toLocaleTimeString();
      const total = Array.isArray(items) ? items.length : 0;
      const shown = Math.min(total, SCOREBOARD_MAX_RANK);

      if (meta) {
        meta.textContent =
          `最終更新: ${now} / チーム数: ${total} / 表示: ${shown}${total > SCOREBOARD_MAX_RANK ? ` + 残り（折りたたみ）` : ""}`;
      }
    } catch (e) {
      if (meta) meta.textContent = `更新失敗: ${e.message}`;
    }
  };

  const start = () => {
    if (timer) clearInterval(timer);
    timer = setInterval(refresh, 15000); // 15秒
  };

  const stop = () => {
    if (timer) clearInterval(timer);
    timer = null;
  };

  refreshBtn?.addEventListener("click", refresh);
  auto?.addEventListener("change", (ev) => {
    if (ev.target.checked) start();
    else stop();
  });

  refresh();
  start();
}

// ------------------------
// quizApi functions
// ------------------------
function getNextQuizFn() {
  // ✅ export 名の揺れを吸収する（破壊的変更を避ける）
  const candidates = [
    "nextQuiz",
    "next",
    "newQuiz",
    "createQuiz",
    "generateQuiz",
    "hostNextQuiz",
    "startQuiz",
    "beginQuiz",
    "issueQuiz",
    "makeQuiz",
  ];

  for (const name of candidates) {
    const fn = quizApi?.[name];
    if (typeof fn === "function") {
      console.log(`[mainHost] using quizApi.${name} as nextQuiz()`);
      return fn;
    }
  }

  // 最後の手段：export されてる関数一覧をログに出す（原因特定用）
  const exportedFns = Object.keys(quizApi || {}).filter((k) => typeof quizApi[k] === "function");
  console.warn("[mainHost] nextQuiz-like function not found. exported functions:", exportedFns);

  return null;
}

window.addEventListener("DOMContentLoaded", async () => {
  setLoading(true);
  try {
    await loadConfig();
  } finally {
    await ensureApiEndpoint();
    setLoading(false);
  }

  // HostKey UI（export有無に関わらず動く）
  const hk = el("hostKey");
  if (hk) {
    hk.value = getHostKeySafe() || "";
    setHostKeySafe(hk.value);
    hk.addEventListener("input", (e) => {
      const v = e?.target?.value ?? "";
      hk.value = v;
      setHostKeySafe(v);
    });
  }

  const nextFn = getNextQuizFn();
  setupShortcuts({ nextQuiz: nextFn || (() => toast("nextQuiz が見つかりません")), stopMic: () => {}, toast });

  const warnNoNext = () => {
    toast("nextQuiz が見つかりません（Consoleにexport一覧を出しました）");
    const exportedFns = Object.keys(quizApi || {}).filter((k) => typeof quizApi[k] === "function");
    console.warn("[mainHost] cannot run nextQuiz. exported functions:", exportedFns);
  };

  el("nextBtn")?.addEventListener("click", () => (nextFn ? nextFn() : warnNoNext()));
  el("nextBtnTop")?.addEventListener("click", () => (nextFn ? nextFn() : warnNoNext()));

  el("resetBtn")?.addEventListener("click", () => resetAll({ clearGlobalMsg, toast }));
  el("copyQidBtn")?.addEventListener("click", copyQid);
  el("sidebarToggle")?.addEventListener("click", toggleSidebar);

  setConn(!!state.apiEndpoint, state.apiEndpoint ? "設定OK" : "未接続");

  setupScoreboard();
});
