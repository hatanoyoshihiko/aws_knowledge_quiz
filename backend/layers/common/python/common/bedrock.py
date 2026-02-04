from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class BedrockClient:
    def __init__(
        self,
        client,
        model_id: str,
        prompt_arn: Optional[str] = None,
        guardrail_identifier: Optional[str] = None,
        guardrail_version: Optional[str] = None,
    ):
        self._client = client
        self._model_id = model_id
        self._prompt_arn = prompt_arn
        self._guardrail_identifier = guardrail_identifier
        self._guardrail_version = guardrail_version

    def converse(self, *, messages: list[dict[str, Any]], **kwargs) -> dict[str, Any]:
        # guardrailが設定されている場合は追加
        if self._guardrail_identifier and self._guardrail_version:
            kwargs.setdefault("guardrailConfig", {
                "guardrailIdentifier": self._guardrail_identifier,
                "guardrailVersion": self._guardrail_version,
            })
        response = self._client.converse(modelId=self._model_id, messages=messages, **kwargs)
        
        # guardrailがブロックした場合の処理
        stop_reason = response.get("stopReason")
        if stop_reason == "guardrail_intervened":
            logger.warning("Guardrail intervened in the response")
            # guardrailの詳細情報をログに記録
            trace = response.get("trace", {})
            guardrail_trace = trace.get("guardrail")
            if guardrail_trace:
                logger.info(f"Guardrail trace: {guardrail_trace}")
        
        return response

    def converse_json(self, *, messages: list[dict[str, Any]], **kwargs) -> dict[str, Any]:
        raw = self.converse(messages=messages, **kwargs)
        
        # guardrailがブロックした場合の処理
        stop_reason = raw.get("stopReason")
        if stop_reason == "guardrail_intervened":
            # guardrailによってブロックされた場合は、エラーではなく特別なレスポンスを返す
            return {
                "error": "guardrail_blocked",
                "message": "Content was blocked by guardrail policy",
                "stopReason": stop_reason,
            }
        
        text = _extract_assistant_text(raw)
        return json.loads(_extract_json_object(text))

    # ★追加：Prompt management の prompt version ARN を使って呼び出す
    def converse_prompt_json(
        self,
        *,
        prompt_arn: str,
        prompt_variables: dict[str, object],
        messages: Optional[list[dict[str, Any]]] = None,
    ) -> str:
        """
        Prompt management の prompt version ARN を modelId に指定して Converse する。

        Bedrock の promptVariables は各値が dict 形式（例: {"text": "..."}）を要求する。
        既存実装（get_next_quiz）は str を渡してくるため、ここで後方互換の変換を行う。

        返り値は assistant テキスト（JSON文字列）を返す。
        """
        if not isinstance(prompt_arn, str) or not prompt_arn.strip():
            raise ValueError("prompt_arn is required")
        if not isinstance(prompt_variables, dict):
            raise ValueError("prompt_variables must be dict")

        def _coerce_var(v: object) -> dict[str, Any]:
            # すでに dict ならそのまま（新形式）
            if isinstance(v, dict):
                return v
            # 文字列/数値/真偽などは text に寄せる（旧形式互換）
            if v is None:
                return {"text": ""}
            if isinstance(v, (str, int, float, bool)):
                return {"text": str(v)}
            # それ以外（list等）は JSON 文字列にする
            try:
                return {"text": json.dumps(v, ensure_ascii=False)}
            except Exception:
                return {"text": str(v)}

        prompt_vars_norm = {k: _coerce_var(v) for k, v in prompt_variables.items() if isinstance(k, str)}

        req: dict[str, Any] = {
            "modelId": prompt_arn.strip(),
            "promptVariables": prompt_vars_norm,
        }
        if messages:
            req["messages"] = messages

        # guardrailが設定されている場合は追加
        if self._guardrail_identifier and self._guardrail_version:
            req["guardrailConfig"] = {
                "guardrailIdentifier": self._guardrail_identifier,
                "guardrailVersion": self._guardrail_version,
            }

        raw = self._client.converse(**req)
        
        # guardrailがブロックした場合の処理
        stop_reason = raw.get("stopReason")
        if stop_reason == "guardrail_intervened":
            logger.warning("Guardrail intervened in prompt response")
            # guardrailによってブロックされた場合は空のJSONを返す
            return '{"error": "guardrail_blocked", "message": "Content was blocked by guardrail policy"}'
        
        return _extract_assistant_text(raw)


    # ★追加：prompt name（またはID）から最新の version ARN を引く（必要なら）
    def resolve_latest_prompt_version_arn(self, *, prompt_name: str) -> str:
        """
        bedrock-agent の ListPrompts/GetPrompt を使って prompt の最新 version ARN を返す。
        prompt_name が ARN ならそのまま返す。
        """
        if not isinstance(prompt_name, str) or not prompt_name.strip():
            raise ValueError("prompt_name is required")
        p = prompt_name.strip()
        if p.startswith("arn:"):
            return p

        agent = __import__("boto3").client("bedrock-agent")
        resp = agent.list_prompts(promptIdentifier=p)

        summaries = resp.get("promptSummaries") or []
        best_arn = None
        best_ver = -1

        for s in summaries:
            if not isinstance(s, dict):
                continue
            arn = s.get("arn")
            ver = s.get("version")
            # version は数値文字列のことが多いので数値化して比較
            try:
                ver_i = int(str(ver))
            except Exception:
                continue
            if isinstance(arn, str) and arn and ver_i > best_ver:
                best_ver = ver_i
                best_arn = arn

        if not best_arn:
            raise RuntimeError(f"Could not resolve latest prompt version arn for promptIdentifier={p}")
        return best_arn


def _extract_assistant_text(raw: dict[str, Any]) -> str:
    """
    Bedrock Converse の response から assistant の text を連結して返す。
    """
    parts = raw.get("output", {}).get("message", {}).get("content", [])
    out = []
    if isinstance(parts, list):
        for p in parts:
            if isinstance(p, dict) and "text" in p and isinstance(p["text"], str):
                out.append(p["text"])
    return "".join(out).strip()


def _extract_json_object(s: str) -> str:
    """
    文字列中の最初の { から対応する } までを抜き出す簡易パーサ。
    """
    start = s.find("{")
    if start == -1:
        raise ValueError("No JSON object start '{' found in model output")

    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue

        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start : i + 1]

    raise ValueError("Unclosed JSON object in model output")
