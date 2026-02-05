from __future__ import annotations
import json
import logging
from typing import Any, Optional
import boto3
from botocore.config import Config

logger = logging.getLogger(__name__)


class BedrockClient:
    def __init__(
        self,
        client=None,
        model_id: Optional[str] = None,
        prompt_arn: Optional[str] = None,
        guardrail_identifier: Optional[str] = None,
        guardrail_version: Optional[str] = None,
    ):
        """
        BedrockClient constructor.
        
        If client is provided, use it (new style with guardrail support).
        Otherwise, create default clients with timeout config (legacy style).
        """
        if client is not None:
            # New style: use provided client
            self._client = client
            self._model_id = model_id
            self._prompt_arn = prompt_arn
            self._guardrail_identifier = guardrail_identifier
            self._guardrail_version = guardrail_version
            self._rt = None
            self._agent = None
            self._cached_prompt_version_arn: str | None = None
        else:
            # Legacy style: create clients with timeout config
            bedrock_config = Config(
                connect_timeout=5,
                read_timeout=25,
                retries={'max_attempts': 1}
            )
            self._rt = boto3.client("bedrock-runtime", config=bedrock_config)
            self._agent = boto3.client("bedrock-agent")
            self._cached_prompt_version_arn: str | None = None
            self._client = None
            self._model_id = model_id
            self._prompt_arn = prompt_arn
            self._guardrail_identifier = guardrail_identifier
            self._guardrail_version = guardrail_version

    def converse(self, *, messages: list[dict[str, Any]], **kwargs) -> dict[str, Any]:
        """Converse API call with optional guardrail support."""
        if self._client is None:
            raise RuntimeError("converse() requires client to be provided in constructor")
        
        # guardrailが設定されている場合は追加
        if self._guardrail_identifier and self._guardrail_version:
            kwargs.setdefault(
                "guardrailConfig",
                {
                    "guardrailIdentifier": self._guardrail_identifier,
                    "guardrailVersion": self._guardrail_version,
                },
            )

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

    def converse_json(
        self,
        *,
        messages: Optional[list[dict[str, Any]]] = None,
        model_id: Optional[str] = None,
        system: Optional[str] = None,
        user: Optional[str] = None,
        **kwargs
    ) -> dict[str, Any] | str:
        """
        Converse API call for JSON responses.
        
        Supports two modes:
        1. New style: messages parameter (with guardrail support)
        2. Legacy style: model_id, system, user parameters
        """
        if messages is not None:
            # New style with guardrail support
            raw = self.converse(messages=messages, **kwargs)

            # guardrailがブロックした場合の処理
            stop_reason = raw.get("stopReason")
            if stop_reason == "guardrail_intervened":
                return {
                    "error": "guardrail_blocked",
                    "message": "Content was blocked by guardrail policy",
                    "stopReason": stop_reason,
                }

            text = _extract_assistant_text(raw)
            return json.loads(_extract_json_object(text))
        
        elif model_id and user:
            # Legacy style
            if self._rt is None:
                raise RuntimeError("Legacy converse_json requires _rt client")
            
            messages_list = [
                {
                    "role": "user",
                    "content": [{"text": user}]
                }
            ]

            req = {
                "modelId": model_id,
                "messages": messages_list,
            }
            
            if system:
                req["system"] = [{"text": system}]

            resp = self._rt.converse(**req)

            parts: list[str] = []
            for c in resp["output"]["message"]["content"]:
                if "text" in c:
                    parts.append(c["text"])
            return "".join(parts).strip()
        
        else:
            raise ValueError("Either 'messages' or 'model_id + user' must be provided")

    def converse_prompt_json(
        self,
        *,
        prompt_arn: str,
        prompt_variables: dict[str, object],
        messages: Optional[list[dict[str, Any]]] = None,
        json_schema: Optional[dict[str, Any]] = None,
    ) -> str:
        """
        Prompt management の prompt version ARN を modelId に指定して Converse する。
        Bedrock の promptVariables は各値が dict 形式（例: {"text": "..."}）を要求する。
        既存実装（get_next_quiz）は str を渡してくるため、ここで後方互換の変換を行う。
        返り値は assistant テキスト（JSON文字列）を返す。
        
        json_schema を指定すると Structured Output を使用（推奨）。
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

        # Structured Output support
        if json_schema:
            req["outputConfig"] = {
                "textFormat": {
                    "type": "json_schema",
                    "structure": {
                        "jsonSchema": {
                            "schema": json.dumps(json_schema, ensure_ascii=False),
                            "name": "quiz_generation",
                            "description": "AWS quiz generation output"
                        }
                    }
                }
            }

        # Use appropriate client
        client = self._client if self._client is not None else self._rt
        if client is None:
            raise RuntimeError("No client available for converse_prompt_json")

        # guardrailが設定されている場合は追加
        if self._guardrail_identifier and self._guardrail_version:
            req["guardrailConfig"] = {
                "guardrailIdentifier": self._guardrail_identifier,
                "guardrailVersion": self._guardrail_version,
            }

        raw = client.converse(**req)

        # guardrailがブロックした場合の処理
        stop_reason = raw.get("stopReason")
        if stop_reason == "guardrail_intervened":
            logger.warning("Guardrail intervened in prompt response")
            # guardrailによってブロックされた場合は空のJSONを返す
            return '{"error": "guardrail_blocked", "message": "Content was blocked by guardrail policy"}'

        return _extract_assistant_text(raw)

    def resolve_latest_prompt_version_arn(self, *, prompt_name: str) -> str:
        """
        Resolve latest PromptVersion ARN by prompt name.
        Cached per execution environment.
        """
        if self._cached_prompt_version_arn:
            return self._cached_prompt_version_arn

        if not prompt_name.strip():
            raise ValueError("prompt_name is empty")

        # Use appropriate agent client
        agent = self._agent if self._agent is not None else boto3.client("bedrock-agent")

        # 1) find prompt by name
        next_token = None
        prompt_arn = None
        while True:
            kwargs = {"maxResults": 50}
            if next_token:
                kwargs["nextToken"] = next_token
            resp = agent.list_prompts(**kwargs)

            for p in resp.get("promptSummaries", []):
                if (p.get("name") or "").strip() == prompt_name.strip():
                    prompt_arn = p.get("arn")
                    break
            if prompt_arn:
                break

            next_token = resp.get("nextToken")
            if not next_token:
                break

        if not prompt_arn:
            raise ValueError(f"Prompt not found: name={prompt_name}")

        # 2) list versions and pick max version
        next_token = None
        latest = None  # dict with arn, version
        while True:
            kwargs = {"promptArn": prompt_arn, "maxResults": 50}
            if next_token:
                kwargs["nextToken"] = next_token
            resp = agent.list_prompt_versions(**kwargs)

            for v in resp.get("promptVersionSummaries", []):
                ver = v.get("version")
                arn = v.get("arn")
                if ver is None or arn is None:
                    continue
                if (latest is None) or (int(ver) > int(latest["version"])):
                    latest = {"version": int(ver), "arn": arn}

            next_token = resp.get("nextToken")
            if not next_token:
                break

        if not latest:
            raise ValueError(f"No PromptVersion exists for prompt: {prompt_arn}")

        self._cached_prompt_version_arn = latest["arn"]
        return latest["arn"]


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
