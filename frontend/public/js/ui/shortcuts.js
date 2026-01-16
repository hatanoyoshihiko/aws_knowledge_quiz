import { el } from "../util/dom.js";

export function setupShortcuts({ nextQuiz, submitAnswer, stopMic, toast }) {
  document.addEventListener("keydown", (e) => {
    const isMac = navigator.platform.toUpperCase().includes("MAC");
    const mod = isMac ? e.metaKey : e.ctrlKey;

    if (mod && e.key === "Enter") {
      e.preventDefault();
      if (typeof submitAnswer === "function") submitAnswer();
      return;
    }

    if (mod && (e.key.toLowerCase() === "k")) {
      e.preventDefault();
      const a = el("answer");
      if (a) {
        a.focus();
        toast?.("回答欄へフォーカス");
      }
      return;
    }

    if (!mod && e.key.toLowerCase() === "n") {
      const tag = (document.activeElement?.tagName || "").toLowerCase();
      if (tag === "textarea" || tag === "input") return;
      e.preventDefault();
      if (typeof nextQuiz === "function") nextQuiz();
      return;
    }

    if (e.key === "Escape") {
      if (typeof stopMic === "function") stopMic();
      return;
    }
  });
}
