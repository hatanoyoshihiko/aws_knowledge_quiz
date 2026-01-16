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
    localStorage.setItem(HOST_KEY_STORAGE, v);
  } catch (_) {}
}

function getHostKeySafe() {
  if (typeof quizApi.getHostKey === "function") {
    return quizApi.getHostKey();
  }
  try {
    return (localStorage.getItem(HOST_KEY_STORAGE) || "").trim();
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

function renderScoreboard(items) {
  const root = el("scoreboardList");
  if (!root) return;

  root.innerHTML = "";

  const sorted = [...(items || [])].sort((a, b) => Number(b.score || 0) - Number(a.score || 0));
  const top = sorted.slice(0, 5);

  for (let i = 0; i < top.length; i++) {
    const t = top[i] || {};
    const team = escapeHtml(t.teamName || t.name || t.teamId || `Team ${i + 1}`);
    const score = Number(t.score ?? t.pts ?? 0) || 0;

    const row = document.createElement("div");
    row.className =
      "flex items-center justify-between gap-3 rounded-xl border border-slate-800 bg-slate-900/60 px-4 py-3";

    row.innerHTML = `
      <div class="flex items-center gap-3">
        <div class="text-lg">${rankBadge(i)}</div>
        <div class="min-w-0">
          <div class="truncate font-semibold text-slate-100">${team}</div>
          <div class="text-xs text-slate-400">${escapeHtml(String(t.teamId || ""))}</div>
        </div>
      </div>
      <div class="text-right">
        <div class="text-xl font-bold text-slate-100">${score}</div>
        <div class="text-xs text-slate-400">pts</div>
      </div>
    `;

    root.appendChild(row);
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
      if (meta) meta.textContent = `最終更新: ${new Date().toLocaleTimeString()} / チーム数: ${items.length}`;
    } catch (e) {
      if (meta) meta.textContent = `更新失敗: ${e.message}`;
    }
  };

  const start = () => {
    if (timer) clearInterval(timer);
    timer = setInterval(refresh, 30000); //30秒
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
