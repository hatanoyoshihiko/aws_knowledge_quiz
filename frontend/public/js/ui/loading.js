import { el } from "../util/dom.js";
import { state } from "../state/state.js";

// safely set .disabled if element exists
function setDisabled(id, disabled) {
  const node = el(id);
  if (!node) return;
  node.disabled = disabled;
}

export function setLoading(isLoading) {
  // host/team で存在しない要素があるので、全部 null-safe にする
  setDisabled("nextBtn", isLoading);
  setDisabled("nextBtnTop", isLoading);

  // team only (or pages that have answer workflow)
  setDisabled("submitBtn", isLoading || !state.questionId);
  setDisabled("micBtn", isLoading);
  setDisabled("clearBtn", isLoading);
  setDisabled("focusAnswerBtn", isLoading);

  const pill = el("loadingPill");
  if (pill) {
    pill.classList.toggle("hidden", !isLoading);
    pill.classList.toggle("flex", isLoading);
  }
}
