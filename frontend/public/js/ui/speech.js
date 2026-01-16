import { el } from "../util/dom.js";
import { state } from "../state/state.js";
import { showGlobalMsg } from "../ui/globalMsg.js";

export function setupSpeech() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    el("micStatus").classList.remove("hidden");
    el("micStatus").textContent = "このブラウザは音声入力（Web Speech API）に未対応です。";
    el("micBtn").disabled = true;
    return;
  }

  const rec = new SpeechRecognition();
  rec.lang = "ja-JP";
  rec.interimResults = true;
  rec.continuous = false;
  rec.maxAlternatives = 1;

  rec.onstart = () => {
    state.recognizing = true;
    el("micStatus").classList.remove("hidden");
    el("micStatus").textContent = "音声入力中…（話し終えたら自動で止まります）";
    el("micBtn").textContent = "■ 停止";
  };

  rec.onerror = (ev) => {
    state.recognizing = false;
    el("micStatus").classList.remove("hidden");
    el("micStatus").textContent = `音声入力エラー: ${ev.error || "unknown"}`;
    el("micBtn").textContent = "🎙 音声入力";
  };

  rec.onend = () => {
    state.recognizing = false;
    el("micBtn").textContent = "🎙 音声入力";
  };

  rec.onresult = (event) => {
    let finalText = "";
    let interimText = "";

    for (let i = event.resultIndex; i < event.results.length; i++) {
      const res = event.results[i];
      if (res.isFinal) finalText += res[0].transcript;
      else interimText += res[0].transcript;
    }

    const current = el("answer").value;
    const sep = (current && !current.endsWith("\n")) ? "\n" : "";
    if (finalText) {
      el("answer").value = current + sep + finalText.trim();
      el("micStatus").textContent = "音声入力が反映されました。必要なら続けて話してください。";
    } else if (interimText) {
      el("micStatus").textContent = `認識中: ${interimText}`;
    }
  };

  state.recognition = rec;
}

export function toggleMic() {
  if (!state.recognition) return;

  if (state.recognizing) {
    try { state.recognition.stop(); } catch (_) {}
  } else {
    try { state.recognition.start(); } catch (_) {
      showGlobalMsg("音声入力を開始できませんでした（ブラウザ権限を確認してください）。");
    }
  }
}

export function stopMic() {
  if (!state.recognition) return;
  try { state.recognition.stop(); } catch (_) {}
}
