import {state} from "./state/state.js";

import {loadConfig} from "./api/config.js";
import * as quizApi from "./api/quizApi.team.js";

import {el} from "./util/dom.js";
import {setLoading} from "./ui/loading.js";
import {setConn} from "./ui/connection.js";
import {toast} from "./ui/toast.js";
import {clearGlobalMsg} from "./ui/globalMsg.js";
import {resetAll} from "./ui/questionView.js";
import {resetResult, wirePointsToggle} from "./ui/resultView.js";
import {setupSpeech, toggleMic, stopMic} from "./ui/speech.js";
import {copyQid} from "./ui/clipboard.js";
import {toggleSidebar} from "./ui/sidebar.js";
import {setupShortcuts} from "./ui/shortcuts.js";
import {loadHistory, clearHistory} from "./state/history.js";

// タブ単位でチーム名を保持（localStorageだとタブ間で上書きされる）
const TEAM_NAME_STORAGE = "awsQuizTeamName_session";
const POLL_MS = 15000; // 15秒

function _normalizeTeamName(name) {
    const n = String(name || "").trim();
    return n || "Team";
}

// ------------------------
// teamId hashing
// ------------------------
function _toHex(uint8arr) {
    return Array.from(uint8arr).map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function _teamIdFromName(teamName) {
    const normalized = _normalizeTeamName(teamName).toLowerCase();
    if (! normalized) 
        return "team:anonymous";
    


    if (!globalThis.crypto ?. subtle ?. digest) {
        const n = normalized.replace(/\s+/g, "-").replace(/[^a-z0-9\-_]/g, "");
        return `team:${
            n || "team"
        }`;
    }

    const enc = new TextEncoder();
    const data = enc.encode(normalized);
    const hashBuf = await crypto.subtle.digest("SHA-256", data);
    const hashHex = _toHex(new Uint8Array(hashBuf));
    return `team:${hashHex}`;
}

function _restoreTeamName() {
    const n = el("teamName");
    if (! n) 
        return;
    


    try {
        n.value = sessionStorage.getItem(TEAM_NAME_STORAGE) || "";
    } catch (_) {}n.addEventListener("input", () => {
        try {
            sessionStorage.setItem(TEAM_NAME_STORAGE, (n.value || "").trim());
        } catch (_) {}
    });
}

// ------------------------
// quizApi の関数解決（安全側に倒す）
// ------------------------
function _getCurrentQuizFn() {
    if (typeof quizApi.currentQuiz === "function") 
        return quizApi.currentQuiz;
    


    return null;
}
function _getSubmitAnswerFn() {
    if (typeof quizApi.submitAnswer === "function") 
        return quizApi.submitAnswer;
    


    return null;
}
function _getQuizByIdFn() {
    if (typeof quizApi.quizById === "function") 
        return quizApi.quizById;
    


    return null;
}
function _getExitReviewFn() {
    if (typeof quizApi.exitReviewMode === "function") 
        return quizApi.exitReviewMode;
    


    return null;
}

// ------------------------
// 画面上のモード表示（任意・安全）
// ------------------------
function _renderModeBadge() {
    const meta = el("qMeta");
    if (! meta) 
        return;
    


    const mode = state.quizMode || "live";
    const suffix = mode === "review" ? "（復習モード）" : "（最新同期）";
    const base = meta.textContent ? meta.textContent.split("（")[0].trim() : "—";
    meta.textContent = `${base} ${suffix}`.trim();
}

// polling は LIVE のときだけ
async function _poll() {
    const fn = _getCurrentQuizFn();
    if (! fn) 
        return;
    


    // quizApi 側も review ガードしているが、ここでも余計な通信を避ける
    if ((state.quizMode || "live") === "review") 
        return;
    


    try {
        await fn({silent: true});
    } catch (_) { // polling では失敗しても無視
    } finally {
        _renderModeBadge();
    }
}

// ------------------------
// Config fallback (robust)
// ------------------------
async function ensureApiEndpoint() {
    if (state.apiEndpoint && String(state.apiEndpoint).trim() !== "") 
        return;
    


    const candidates = [
        new URL("config.json", window.location.href).toString(),
        new URL("./config.json", window.location.href).toString(),
        new URL("../config.json", window.location.href).toString(),
        new URL("/config.json", window.location.origin).toString(),
    ];

    for (const url of candidates) {
        try {
            const res = await fetch(url, {cache: "no-store"});
            if (! res.ok) 
                continue;
            


            const cfg = await res.json();
            if (cfg ?. apiEndpoint && String(cfg.apiEndpoint).trim() !== "") {
                state.apiEndpoint = cfg.apiEndpoint;
                return;
            }
        } catch (_) {}
    }

    // CloudFront 経由アクセス前提の構成では "." が正解（./quiz/...）
    state.apiEndpoint = ".";
}

// ------------------------
// Score submit
// ------------------------
async function submitScoreDelta({
    apiBase,
    teamId,
    teamName,
    questionId,
    delta,
    result,
    score,
    feedback
}) {
    const url = `${apiBase}/scores/submit`;
    const payload = {
        teamId,
        teamName,
        questionId,
        delta,
        result,
        score,
        feedback
    };

    const res = await fetch(url, {
        method: "POST",
        headers: {
            "Content-Type": "application/json"
        },
        body: JSON.stringify(payload)
    });

    const text = await res.text();
    let data = null;
    try {
        data = text ? JSON.parse(text) : null;
    } catch (_) {
        data = null;
    }

    if (! res.ok) {
        const msg = `[scores/submit] HTTP ${
            res.status
        } ${
            res.statusText
        } ${
            (text || "").slice(0, 200)
        }`;
        throw new Error(msg);
    }

    return data ?? {
        ok: true
    };
}

// ------------------------
// Score parsing (from UI)
// ------------------------
function _getScore100FromScoreText() {
    const raw = (el("scoreText") ?. textContent || "").trim();
    const m = raw.match(/score:\s*(\d{1,3})/i);
    if (! m) 
        return null;
    


    const n = Number(m[1]);
    if (!Number.isFinite(n)) 
        return null;
    


    return Math.max(0, Math.min(100, Math.round(n)));
}

// ------------------------
// 二重送信・二重加算防止
// ------------------------
function _getCurrentQidFromDom() {
    const ids = [
        "qid",
        "qidText",
        "questionId",
        "quizId",
        "currentQid",
        "qId"
    ];
    for (const id of ids) {
        const node = el(id);
        const t = node ?. textContent ? String(node.textContent).trim() : "";
        if (t && t !== "—") 
            return t;
        


    }
    return null;
}

// 採点結果の詳細情報を取得
function _getJudgmentDetails() { // result (correct/close/incorrect)
    const resultEl = el("result");
    const result = resultEl ?. textContent ?. trim() || null;

    // score (0.0-1.0)
    const scoreEl = el("scoreText");
    const scoreText = scoreEl ?. textContent || "";
    const scoreMatch = scoreText.match(/score:\s*([\d.]+)/i);
    const score = scoreMatch ? parseFloat(scoreMatch[1]) / 100 : null;

    // feedback
    const feedbackEl = el("feedback");
    const feedback = feedbackEl ?. textContent ?. trim() || null;

    return {result, score, feedback};
}

let _submitInFlight = false;
// 二重加算防止はバックエンドで管理（フロントでは削除）

// ------------------------
// Main action: judge -> read scoreText -> submit score
// ------------------------
async function submitAnswerAndScore() {
    const apiBase = state.apiEndpoint;
    if (! apiBase || String(apiBase).trim() === "") {
        toast("API未接続（config.json）");
        return;
    }

    const submitFn = _getSubmitAnswerFn();
    if (! submitFn) {
        toast("submitAnswer が見つかりません（quizApi.team.js の export を確認してください）");
        return;
    }

    // state.questionId のチェックを追加
    if (!state.questionId) {
        toast("先にクイズを取得してください");
        return;
    }

    const teamName = _normalizeTeamName(el("teamName") ?. value);
    const teamId = await _teamIdFromName(teamName);

    const qid = _getCurrentQidFromDom();
    if (! qid) {
        toast("問題IDが取得できません");
        return;
    }

    if (_submitInFlight) 
        return;
    


    _submitInFlight = true;

    try {
        try {
            await submitFn();
        } catch (e) {
            toast("採点に失敗しました（Console参照）");
            return;
        }

        const delta = _getScore100FromScoreText();
        if (delta == null || delta <= 0) {
            toast("0点のためスコアは加算されません");
            return;
        }

        // 採点結果の詳細情報を取得
        const {result, score, feedback} = _getJudgmentDetails();

        try {
            const response = await submitScoreDelta({
                apiBase,
                teamId,
                teamName,
                questionId: qid,
                delta,
                result,
                score,
                feedback
            });

            // バックエンドからのメッセージを表示
            if (response.isFirstAnswer) {
                toast(`スコア送信: ${teamName} +${delta}pts（初回回答）`);
            } else {
                toast(`再評価完了: ${teamName}（スコアは初回の${
                    response.firstDelta
                }ptsを維持）`);
            }
        } catch (e) {
            toast(`スコア送信失敗: ${
                String(e.message || e).slice(0, 140)
            }`);
        }
    } finally {
        _submitInFlight = false;
    }
}

// ------------------------
// Boot
// ------------------------
window.addEventListener("DOMContentLoaded", async () => { // state 初期化（無くてもOK）
    state.quizMode = "live";

    setLoading(true);
    try {
        await loadConfig();
    } finally {
        await ensureApiEndpoint();
        setLoading(false);
    } _restoreTeamName();
    setupSpeech();
    loadHistory();
    wirePointsToggle();
    _renderModeBadge();

    setupShortcuts({
        submitAnswer: submitAnswerAndScore,
        stopMic,
        toast,
        focusAnswer: () => el("answer") ?. focus()
    });

    el("submitBtn") ?. addEventListener("click", submitAnswerAndScore);
    el("micBtn") ?. addEventListener("click", toggleMic);

    const currentFn = _getCurrentQuizFn();
    const quizByIdFn = _getQuizByIdFn();
    const exitReviewFn = _getExitReviewFn();

    // 更新ボタン＝「最新へ戻る（LIVE）」＋即時同期
    const refresh = async () => { // ★復習解除 → ★強制同期（同一ID判定を飛ばす保険）
        if (exitReviewFn) 
            exitReviewFn({silent: true});
        


        state.quizMode = "live";
        _renderModeBadge();

        if (! currentFn) 
            return toast("currentQuiz が見つかりません（quizApi.team.js を確認）");
        


        await currentFn({silent: false, force: true});
        _renderModeBadge();
    };

    el("refreshBtn") ?. addEventListener("click", refresh);
    el("refreshBtnSide") ?. addEventListener("click", refresh);

    // 履歴クリックイベント（REVIEW）
    window.addEventListener("quiz:review", async (ev) => {
        const qid = ev ?. detail ?. questionId;
        if (! qid) 
            return;
        


        // quizApi に寄せる（DOM直書きをやめる）
        if (! quizByIdFn) 
            return toast("quizById が見つかりません（quizApi.team.js を確認）");
        


        try {
            await quizByIdFn(qid, {silent: false});
            _renderModeBadge();
        } catch (e) {
            toast(`復習取得に失敗: ${
                String(e.message || e).slice(0, 140)
            }`);
        }
    });

    el("clearBtn") ?. addEventListener("click", () => {
        const a = el("answer");
        if (a) 
            a.value = "";
        


        resetResult();
        clearGlobalMsg();
        toast("回答をクリアしました");
    });

    el("focusAnswerBtn") ?. addEventListener("click", () => {
        el("answer") ?. focus();
        toast("回答欄へフォーカス");
    });

    el("resetBtn") ?. addEventListener("click", () => resetAll({clearGlobalMsg, toast}));
    el("copyQidBtn") ?. addEventListener("click", copyQid);
    el("sidebarToggle") ?. addEventListener("click", toggleSidebar);
    el("clearHistoryBtn") ?. addEventListener("click", clearHistory);

    setConn(!!state.apiEndpoint, state.apiEndpoint ? "設定OK" : "未接続");

    // 初回同期（LIVE）
    if (currentFn) {
        await currentFn({silent: true, force: true});
        _renderModeBadge();
        setInterval(_poll, POLL_MS);
    } else {
        console.warn("[mainTeam] currentQuiz export not found", quizApi);
    }
});
