import { el } from "../util/dom.js";
import { state } from "../state/state.js";
import { fetchWithTimeout, isAbortError } from "./fetch.js";
import { setConn } from "../ui/connection.js";
import { showGlobalMsg, clearGlobalMsg } from "../ui/globalMsg.js";
import { setLoading } from "../ui/loading.js";
import { setQuestionView } from "../ui/questionView.js";
import { setResult } from "../ui/resultView.js";
import { toast } from "../ui/toast.js";
import { addHistory } from "../state/history.js";

function _unwrapLambdaProxy(data) {
  // { statusCode, headers, body:"..." } を吸収
  if (data && typeof data === "object" && typeof data.body === "string") {
    try {
      const raw = data.isBase64Encoded ? atob(data.body) : data.body;
      return raw ? JSON.parse(raw) : data;
    } catch (_) {
      return data;
    }
  }
  return data;
}

export async function currentQuiz({ silent = false } = {}) {
  if (!silent) clearGlobalMsg();

  if (!state.apiEndpoint) {
    if (!silent) {
      showGlobalMsg("API Endpoint が未設定です（config.json を確認してください）。");
      toast("config.json を確認してください");
    }
    return null;
  }

  const url = `${state.apiEndpoint}/quiz/current`;
  console.log("[quiz] currentQuiz", { url, timeoutMs: state.requestTimeoutMs });

  try {
    const resp = await fetchWithTimeout(url, { method: "GET" });
    const text = await resp.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (_) { data = null; }

    data = _unwrapLambdaProxy(data);

    if (!resp.ok) {
      const msg = data?.error?.message || `HTTP ${resp.status}`;
      if (!silent) showGlobalMsg(msg);
      setConn(false, "エラー");
      return null;
    }

    const q = data?.question;
    if (!q || !q.questionId || !q.title || !q.body) {
      if (!silent) showGlobalMsg("API応答が不正です（question が不足）。");
      setConn(false, "エラー");
      return null;
    }

    // 既に同じクイズなら UI は更新しない（ちらつき防止）
    if (state.questionId && q.questionId === state.questionId) {
      setConn(true, "接続OK");
      return q;
    }

    setConn(true, "接続OK");
    setQuestionView(q);
    if (!silent) toast("最新のクイズに同期しました");
    return q;
  } catch (e) {
    if (isAbortError(e)) {
      if (!silent) showGlobalMsg(`タイムアウトです（${state.requestTimeoutMs}ms）。`);
      setConn(false, "タイムアウト");
    } else {
      if (!silent) showGlobalMsg("通信エラーです（詳細はConsoleログを確認してください）。");
      setConn(false, "エラー");
    }
    return null;
  }
}

export async function submitAnswer() {
  clearGlobalMsg();

  if (!state.apiEndpoint) {
    showGlobalMsg("API Endpoint が未設定です（config.json を確認してください）。");
    return null;
  }
  if (!state.questionId) {
    showGlobalMsg("先にクイズを取得してください。");
    return null;
  }

  const answerText = el("answer").value.trim();
  if (!answerText) {
    showGlobalMsg("回答を入力してください。");
    return null;
  }

  setLoading(true);

  const url = `${state.apiEndpoint}/quiz/answer`;
  const payload = {
    questionId: state.questionId,
    answerText,
    inputMethod: state.recognizing ? "voice" : "text",
  };

  console.log("[quiz] submitAnswer", { url, timeoutMs: state.requestTimeoutMs });

  try {
    const resp = await fetchWithTimeout(url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });

    const text = await resp.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (_) { data = null; }

    data = _unwrapLambdaProxy(data);

    if (!resp.ok) {
      const msg = data?.error?.message || `HTTP ${resp.status}`;
      showGlobalMsg(msg);
      setConn(false, "エラー");
      return null;
    }

    if (!data || typeof data !== "object") {
      showGlobalMsg("API応答が不正です（採点結果が空です）。");
      setConn(false, "エラー");
      return null;
    }

    setConn(true, "接続OK");
    setResult(data);
    toast("採点が完了しました");

    addHistory({
      questionId: state.questionId,
      title: state.currentQuestion?.title || "",
      category: state.currentQuestion?.category || "",
      level: state.currentQuestion?.level || "",
      result: data.result,
      score: data.score,
      at: new Date().toLocaleString("ja-JP"),
    });

    // ★ team 側でスコア送信などに使えるように返す
    return data;
  } catch (e) {
    if (isAbortError(e)) {
      showGlobalMsg(`タイムアウトです（${state.requestTimeoutMs}ms）。`);
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
