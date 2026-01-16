import { state } from "../state/state.js";
import { toast } from "../ui/toast.js";

export async function copyQid() {
  if (!state.questionId) {
    toast("questionId がありません");
    return;
  }
  try {
    await navigator.clipboard.writeText(state.questionId);
    toast("questionId をコピーしました");
  } catch (_) {
    toast("コピーに失敗しました");
  }
}
