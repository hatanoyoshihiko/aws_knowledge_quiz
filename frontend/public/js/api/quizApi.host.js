import { el } from "../util/dom.js";
import { state } from "../state/state.js";
import { fetchWithTimeout, isAbortError } from "./fetch.js";
import { setConn } from "../ui/connection.js";
import { showGlobalMsg, clearGlobalMsg } from "../ui/globalMsg.js";
import { setLoading } from "../ui/loading.js";
import { setQuestionView } from "../ui/questionView.js";
import { toast } from "../ui/toast.js";

// host key
const HOST_KEY_STORAGE = "awsQuizHostKey";

export function setHostKey(key) {
  try {
    localStorage.setItem(HOST_KEY_STORAGE, String(key ?? "").trim());
  } catch (_) {}
}

export function getHostKey() {
  try {
    return (localStorage.getItem(HOST_KEY_STORAGE) || "").trim();
  } catch (_) {
    return "";
  }
}

function _hostHeaders() {
  const k = getHostKey();
  return k ? { "x-host-key": k } : {};
}

/**
 * Host: 次のクイズ生成
 * - UIに category/level セレクトがあればそれを使う
 * - 無ければ空で呼ぶ（バックエンド側デフォルトに任せる）
 */
export async function nextQuiz() {
  clearGlobalMsg();

  if (!state.apiEndpoint) {
    showGlobalMsg("API Endpoint が未設定です（config.json を確認してください）。");
    toast("config.json を確認してください");
    return null;
  }

  setLoading(true);

  // host.html に category/level が無い構成でも落ちないようにする
  const categoryEl = el("category");
  const levelEl = el("level");
  const category = categoryEl ? String(categoryEl.value || "") : "";
  const level = levelEl ? String(levelEl.value || "") : "";

  const qs = new URLSearchParams();
  if (category) qs.set("category", category);
  if (level) qs.set("level", level);

  const url = `${state.apiEndpoint}/quiz/next${qs.toString() ? `?${qs.toString()}` : ""}`;

  console.log("[quiz] nextQuiz", { url, category, level, timeoutMs: state.requestTimeoutMs });

  try {
    const resp = await fetchWithTimeout(url, {
      method: "GET",
      headers: { ..._hostHeaders() },
    });

    const text = await resp.text();

    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch (_) {
      data = null;
    }

    // unwrap Lambda proxy style: { statusCode, headers, body: "..." }
    if (data && typeof data === "object" && typeof data.body === "string") {
      try {
        const raw = data.isBase64Encoded ? atob(data.body) : data.body;
        data = raw ? JSON.parse(raw) : null;
      } catch (_) {}
    }

    if (!resp.ok) {
      const msg = (data && data?.error?.message) ? data.error.message : `HTTP ${resp.status}`;
      showGlobalMsg(msg);
      setConn(false, "エラー");
      return null;
    }

    const q = data?.question;
    if (!q || !q.questionId || !q.title || !q.body) {
      showGlobalMsg("API応答が不正です（question が不足）。");
      setConn(false, "エラー");
      return null;
    }

    setConn(true, "接続OK");
    setQuestionView(q);
    toast("クイズを生成しました");
    return q;
  } catch (e) {
    if (isAbortError(e)) {
      showGlobalMsg(
        `タイムアウトです（${state.requestTimeoutMs}ms）。Bedrock生成が長引いた可能性があります。`
      );
      setConn(false, "タイムアウト");
    } else {
      showGlobalMsg("通信エラーです（詳細はConsoleログを確認してください）。");
      setConn(false, "エラー");
    }
    return null;
  } finally {
    setLoading(false);
  }
}
