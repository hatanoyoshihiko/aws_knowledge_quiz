import {el} from "../util/dom.js";
import {state} from "../state/state.js";
import {fetchWithTimeout, isAbortError} from "./fetch.js";
import {setConn} from "../ui/connection.js";
import {showGlobalMsg, clearGlobalMsg} from "../ui/globalMsg.js";
import {setLoading} from "../ui/loading.js";
import {setQuestionView} from "../ui/questionView.js";
import {setResult} from "../ui/resultView.js";
import {toast} from "../ui/toast.js";
import {addHistory} from "../state/history.js";

function _unwrapLambdaProxy(data) { // { statusCode, headers, body:"..." } を吸収
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

function _ensureModeDefaults() { // state にプロパティが無くても問題ないようにする
    if (!state.quizMode) 
        state.quizMode = "live";
    
    // "live" | "review"
}

function _apiBaseOrNull({silent}) {
    if (!state.apiEndpoint) {
        if (!silent) {
            showGlobalMsg("API Endpoint が未設定です（config.json を確認してください）。");
            toast("config.json を確認してください");
        }
        return null;
    }
    return state.apiEndpoint;
}

async function _getJsonOrNull(resp) {
    const text = await resp.text();
    let data = null;
    try {
        data = text ? JSON.parse(text) : null;
    } catch (_) {
        data = null;
    }
    return _unwrapLambdaProxy(data);
}

function _setCurrentQuestionState(q) { // setQuestionView の内部実装に依存せず state を整合させる
    state.questionId = q.questionId;
    state.currentQuestion = q;
}

function _validateQuestionOrNull(data, {
    silent
}) {
    const q = data ?. question;
    if (! q || ! q.questionId || ! q.title || ! q.body) {
        if (!silent) 
            showGlobalMsg("API応答が不正です（question が不足）。");
        

        setConn(false, "エラー");
        return null;
    }
    return q;
}

export function exitReviewMode({
    silent = true
} = {}) {
    _ensureModeDefaults();
    state.quizMode = "live";
    state.reviewQuestionId = null;
    if (!silent) 
        toast("最新同期モードに戻りました");
    

}

export async function currentQuiz({
    silent = false,
    force = false
} = {}) {
    _ensureModeDefaults();
    if (!silent) 
        clearGlobalMsg();
    


    // ★ review中は “勝手に最新に上書き” しない
    // 更新ボタンでは exitReviewMode() してから currentQuiz() を呼ぶ想定
    if (!force && state.quizMode === "review") {
        setConn(true, "接続OK");
        if (!silent) 
            toast("復習モード中です（更新で最新に戻ります）");
        

        return state.currentQuestion || null;
    }

    const apiBase = _apiBaseOrNull({silent});
    if (! apiBase) 
        return null;
    


    const url = `${apiBase}/quiz/current`;
    console.log("[quiz] currentQuiz", {url, timeoutMs: state.requestTimeoutMs});

    try {
        const resp = await fetchWithTimeout(url, {method: "GET"});
        const data = await _getJsonOrNull(resp);

        if (! resp.ok) {
            const msg = data ?. error ?. message || `HTTP ${
                resp.status
            }`;
            if (!silent) 
                showGlobalMsg(msg);
            

            setConn(false, "エラー");
            return null;
        }

        const q = _validateQuestionOrNull(data, {silent});
        if (! q) 
            return null;
        


        // 既に同じクイズなら UI は更新しない（ちらつき防止）
        // ※ “state.questionId が古い/壊れている” ケースに備え force オプションを用意
        if (!force && state.questionId && q.questionId === state.questionId) {
            setConn(true, "接続OK");
            return q;
        }

        setConn(true, "接続OK");
        _setCurrentQuestionState(q);
        setQuestionView(q);
        if (!silent) 
            toast("最新のクイズに同期しました");
        

        return q;
    } catch (e) {
        if (isAbortError(e)) {
            if (!silent) 
                showGlobalMsg(`タイムアウトです（${
                    state.requestTimeoutMs
                }ms）。`);
            

            setConn(false, "タイムアウト");
        } else {
            if (!silent) 
                showGlobalMsg("通信エラーです（詳細はConsoleログを確認してください）。");
            

            setConn(false, "エラー");
        }
        return null;
    }
}

export async function quizById(questionId, {
    silent = false
} = {}) {
    _ensureModeDefaults();
    if (!silent) 
        clearGlobalMsg();
    


    const qid = String(questionId || "").trim();
    if (! qid) {
        if (!silent) 
            showGlobalMsg("questionId が不正です。");
        

        return null;
    }

    const apiBase = _apiBaseOrNull({silent});
    if (! apiBase) 
        return null;
    


    const url = `${apiBase}/quiz/question?questionId=${
        encodeURIComponent(qid)
    }`;
    console.log("[quiz] quizById", {url, timeoutMs: state.requestTimeoutMs});

    try {
        const resp = await fetchWithTimeout(url, {method: "GET"});
        const data = await _getJsonOrNull(resp);

        if (! resp.ok) {
            const msg = data ?. error ?. message || `HTTP ${
                resp.status
            }`;
            if (!silent) 
                showGlobalMsg(msg);
            

            setConn(false, "エラー");
            return null;
        }

        const q = _validateQuestionOrNull(data, {silent});
        if (! q) 
            return null;
        


        // ★ ここで review モードへ
        state.quizMode = "review";
        state.reviewQuestionId = q.questionId;

        setConn(true, "接続OK");
        _setCurrentQuestionState(q);
        setQuestionView(q);
        if (!silent) 
            toast("復習クイズを表示しました（更新で最新に戻ります）");
        

        return q;
    } catch (e) {
        if (isAbortError(e)) {
            if (!silent) 
                showGlobalMsg(`タイムアウトです（${
                    state.requestTimeoutMs
                }ms）。`);
            

            setConn(false, "タイムアウト");
        } else {
            if (!silent) 
                showGlobalMsg("通信エラーです（詳細はConsoleログを確認してください）。");
            

            setConn(false, "エラー");
        }
        return null;
    }
}

export async function submitAnswer() {
    clearGlobalMsg();

    const apiBase = _apiBaseOrNull({silent: false});
    if (! apiBase) 
        return null;
    


    if (!state.questionId) {
        showGlobalMsg("先にクイズを取得してください。");
        return null;
    }

    const answerText = el("answer").value.trim();
    if (! answerText) {
        showGlobalMsg("回答を入力してください。");
        return null;
    }

    setLoading(true);

    const url = `${apiBase}/quiz/answer`;
    const payload = {
        questionId: state.questionId,
        answerText,
        inputMethod: state.recognizing ? "voice" : "text"
    };

    console.log("[quiz] submitAnswer", {url, timeoutMs: state.requestTimeoutMs});

    try {
        const resp = await fetchWithTimeout(url, {
            method: "POST",
            headers: {
                "content-type": "application/json"
            },
            body: JSON.stringify(payload)
        });

        const data = await _getJsonOrNull(resp);

        if (! resp.ok) {
            const msg = data ?. error ?. message || `HTTP ${
                resp.status
            }`;
            showGlobalMsg(msg);
            setConn(false, "エラー");
            return null;
        }

        if (! data || typeof data !== "object") {
            showGlobalMsg("API応答が不正です（採点結果が空です）。");
            setConn(false, "エラー");
            return null;
        }

        setConn(true, "接続OK");
        console.log("[DEBUG] submitAnswer: calling setResult with data =", data);
        setResult(data);
        console.log("[DEBUG] submitAnswer: setResult completed");
        toast("採点が完了しました");

        addHistory({
            questionId: state.questionId,
            title: state.currentQuestion ?. title || "",
            category: state.currentQuestion ?. category || "",
            level: state.currentQuestion ?. level || "",
            result: data.result,
            score: data.score,
            at: new Date().toLocaleString("ja-JP")
        });

        return data;
    } catch (e) {
        if (isAbortError(e)) {
            showGlobalMsg(`タイムアウトです（${
                state.requestTimeoutMs
            }ms）。`);
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

export async function generateExampleAnswer({
    silent = false
} = {}) {
    if (!silent) 
        clearGlobalMsg();
    


    const apiBase = _apiBaseOrNull({silent});
    if (! apiBase) 
        return null;
    


    if (!state.questionId) {
        if (!silent) 
            showGlobalMsg("先にクイズを取得してください。");
        

        return null;
    }

    const url = `${apiBase}/quiz/example-answer?questionId=${
        encodeURIComponent(state.questionId)
    }`;
    console.log("[quiz] generateExampleAnswer", {url, timeoutMs: state.requestTimeoutMs});

    try {
        const resp = await fetchWithTimeout(url, {method: "GET"});
        const data = await _getJsonOrNull(resp);

        if (! resp.ok) {
            const msg = data ?. error ?. message || `HTTP ${
                resp.status
            }`;
            if (!silent) 
                showGlobalMsg(msg);
            

            setConn(false, "エラー");
            return null;
        }

        if (! data || ! data.exampleAnswer) {
            if (!silent) 
                showGlobalMsg("API応答が不正です（回答例が空です）。");
            

            setConn(false, "エラー");
            return null;
        }

        setConn(true, "接続OK");
        if (!silent) 
            toast("回答例を生成しました");
        

        return data.exampleAnswer;
    } catch (e) {
        if (isAbortError(e)) {
            if (!silent) 
                showGlobalMsg(`タイムアウトです（${
                    state.requestTimeoutMs
                }ms）。`);
            

            setConn(false, "タイムアウト");
        } else {
            if (!silent) 
                showGlobalMsg("通信エラーです（詳細はConsoleログを確認してください）。");
            

            setConn(false, "エラー");
        }
        return null;
    }
}
