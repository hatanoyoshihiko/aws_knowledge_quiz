import { state } from "./state/state.js";

import { loadConfig } from "./api/config.js";
import { nextQuiz, submitAnswer } from "./api/quizApi.js";

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

window.addEventListener("DOMContentLoaded", async () => {
  setLoading(false);

  await loadConfig();

  setupSpeech();
  loadHistory();
  setupShortcuts({ nextQuiz, submitAnswer, stopMic, toast });

  // Buttons
  el("nextBtn")?.addEventListener("click", nextQuiz);
  el("nextBtnTop")?.addEventListener("click", nextQuiz);

  el("submitBtn")?.addEventListener("click", submitAnswer);
  el("micBtn")?.addEventListener("click", toggleMic);

  el("clearBtn")?.addEventListener("click", () => {
    el("answer").value = "";
    resetResult();
    clearGlobalMsg();
    toast("回答をクリアしました");
  });

  el("focusAnswerBtn")?.addEventListener("click", () => {
    el("answer").focus();
    toast("回答欄へフォーカス");
  });

  el("resetBtn")?.addEventListener("click", () => resetAll({ clearGlobalMsg, toast }));
  el("copyQidBtn")?.addEventListener("click", copyQid);
  el("sidebarToggle")?.addEventListener("click", toggleSidebar);
  el("clearHistoryBtn")?.addEventListener("click", clearHistory);

  // Collapsible points
  wirePointsToggle();

  // Initial connection display
  setConn(!!state.apiEndpoint, state.apiEndpoint ? "設定OK" : "未接続");
});
