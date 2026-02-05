import {el} from "../util/dom.js";
import {state} from "../state/state.js";
import {fetchWithTimeout, isAbortError} from "./fetch.js";
import {setConn} from "../ui/connection.js";
import {showGlobalMsg, clearGlobalMsg} from "../ui/globalMsg.js";
import {setLoading} from "../ui/loading.js";
import {setQuestionView} from "../ui/questionView.js";
import {toast} from "../ui/toast.js";

// host key
const HOST_KEY_STORAGE = "awsQuizHostKey";

export function setHostKey(key) {
    try {
        localStorage.setItem(HOST_KEY_STORAGE, String(key ?? "").trim());
    } catch (_) {}
}

export function getHostKey() {
    try {
        return(localStorage.getItem(HOST_KEY_STORAGE) || "").trim();
    } catch (_) {
        return "";
    }
}

function _hostHeaders() {
    const k = getHostKey();
    return k ? {
        "x-host-key": k
    } : {};
}

/**
 * Host: 次のクイズ生成（非同期）
 * - 即座に202を返却
 * - バックグラウンドでクイズ生成
 * - 生成完了後、/quiz/currentで取得
 */
export async function nextQuiz() {
    clearGlobalMsg();

    if (!state.apiEndpoint) {
        showGlobalMsg("API Endpoint が未設定です（config.json を確認してください）。");
        toast("config.json を確認してください");
        return null;
    }

    setLoading(true);

    const categoryEl = el("category");
    const levelEl = el("level");
    const category = categoryEl ? String(categoryEl.value || "") : "";
    const level = levelEl ? String(levelEl.value || "") : "";

    const qs = new URLSearchParams();
    if (category) 
        qs.set("category", category);
    


    if (level) 
        qs.set("level", level);
    


    // 新しい非同期エンドポイントを使用
    const url = `${
        state.apiEndpoint
    }/quiz/generate${
        qs.toString() ? `?${
            qs.toString()
        }` : ""
    }`;

    console.log("[quiz] nextQuiz (async)", {url, category, level});

    try { // Step 1: クイズ生成開始
        const resp = await fetchWithTimeout(url, {
            method: "GET",
            headers: {
                ... _hostHeaders()
            }
        }, 10000); // 10秒タイムアウト（即座に返るはず）

        const text = await resp.text();
        let data = null;
        try {
            data = text ? JSON.parse(text) : null;
        } catch (_) {
            data = null;
        }

        if (data && typeof data === "object" && typeof data.body === "string") {
            try {
                const raw = data.isBase64Encoded ? atob(data.body) : data.body;
                data = raw ? JSON.parse(raw) : null;
            } catch (_) {}
        }

        if (! resp.ok) {
            const msg = (data && data ?. error ?. message) ? data.error.message : `HTTP ${
                resp.status
            }`;
            showGlobalMsg(msg);
            setConn(false, "エラー");
            return null;
        }

        // 202 Accepted - 生成開始
        if (resp.status === 202) {
            showGlobalMsg("クイズを生成中です。しばらくお待ちください...");
            toast("生成中...");

            // 現在のクイズIDを記録（新しいクイズかどうか判定するため）
            const previousQuizId = state.questionId || null;
            console.log(`[quiz] previous questionId: ${previousQuizId}`);

            // Step 2: ポーリングで最新クイズを取得
            const maxAttempts = 12; // 最大60秒（5秒×12回）
            for (let i = 0; i < maxAttempts; i++) {
                await new Promise(resolve => setTimeout(resolve, 5000)); // 5秒待機

                const currentUrl = `${
                    state.apiEndpoint
                }/quiz/current`;
                console.log(`[quiz] polling attempt ${
                    i + 1
                }/${maxAttempts}`);

                try {
                    const currentResp = await fetchWithTimeout(currentUrl, {
                        method: "GET",
                        headers: {
                            ... _hostHeaders()
                        }
                    }, 10000);

                    const currentText = await currentResp.text();
                    let currentData = null;
                    try {
                        currentData = currentText ? JSON.parse(currentText) : null;
                    } catch (_) {}

                    if (currentData && typeof currentData === "object" && typeof currentData.body === "string") {
                        try {
                            const raw = currentData.isBase64Encoded ? atob(currentData.body) : currentData.body;
                            currentData = raw ? JSON.parse(raw) : null;
                        } catch (_) {}
                    }

                    if (currentResp.ok && currentData ?. question) {
                        const q = currentData.question;
                        // 新しいクイズが生成されたかチェック（IDが変わっている）
                        if (q.questionId && q.title && q.body && q.questionId !== previousQuizId) {
                            console.log(`[quiz] new quiz detected: ${
                                q.questionId
                            }`);
                            clearGlobalMsg();
                            setConn(true, "接続OK");
                            setQuestionView(q);
                            toast("クイズを生成しました");
                            return q;
                        } else if (q.questionId === previousQuizId) {
                            console.log(`[quiz] still old quiz (${
                                q.questionId
                            }), continuing...`);
                        }
                    }
                } catch (pollError) {
                    console.warn(`[quiz] polling attempt ${
                        i + 1
                    } failed:`, pollError);
                }
            }

            // タイムアウト
            showGlobalMsg("クイズ生成がタイムアウトしました。もう一度お試しください。");
            setConn(false, "タイムアウト");
            return null;
        }

        // 200 OK - 同期レスポンス（後方互換）
        const q = data ?. question;
        if (! q || ! q.questionId || ! q.title || ! q.body) {
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
            showGlobalMsg("タイムアウトです。ネットワーク接続を確認してください。");
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
