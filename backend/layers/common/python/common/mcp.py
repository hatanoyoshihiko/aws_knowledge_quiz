from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


# -------------------------
# Tuning knobs (speed vs quality)
# -------------------------
# search_documentation の上位何件だけ採用するか
SEARCH_TOP_K = 3

# recommend を使うか（遅くなりがちなのでデフォルトOFF推奨）
ENABLE_RECOMMEND = False
# recommend を使う場合、上位何件に対して実行するか
RECOMMEND_TOP_K = 1

# read_documentation の上位何件だけ読むか（ここが一番効く）
READ_TOP_K = 3

# 1ページから作るスニペットの最大文字数（Bedrock入力増＝遅延増なので抑えめ）
SNIPPET_MAX_CHARS = 900


@dataclass
class McpSnippet:
    text: str
    source: str | None = None
    url: str | None = None


class McpClientError(RuntimeError):
    """MCP 呼び出し失敗（ネットワーク・プロトコル・ツールエラー）。"""


class McpClient:
    _DEFAULT_PROTOCOL_VERSION = "2025-06-18"

    def __init__(
        self,
        endpoint: str,
        api_key: str = "",
        timeout_seconds: float = 12.0,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

        self._session_id: Optional[str] = None
        self._protocol_version: str = self._DEFAULT_PROTOCOL_VERSION
        self._req_id = int(time.time() * 1000) % 1000000
        self._initialized = False

    # -------------------------
    # Public API used by Lambdas
    # -------------------------

    def search(self, *, query: str, max_snippets: int) -> List[McpSnippet]:
        """クイズ生成用の根拠スニペットを作る（高速化版）。

        1) search_documentation の URL を上位 SEARCH_TOP_K に絞る
        2) recommend はデフォルトOFF（必要なら最小回数だけ）
        3) read_documentation は上位 READ_TOP_K のみ読む
        """
        if not query.strip():
            return []

        self._ensure_initialized()

        # 1) search_documentation
        search_args = self._build_search_args(query)
        search_res = self._call_tool("aws___search_documentation", search_args)
        urls = self._extract_urls(search_res)
        urls = urls[:SEARCH_TOP_K]

        # 2) recommend（削減：デフォルトOFF）
        if ENABLE_RECOMMEND and urls:
            for u in urls[: min(RECOMMEND_TOP_K, len(urls))]:
                rec_args = self._build_recommend_args(u)
                rec_res = self._call_tool("aws___recommend", rec_args)
                for ru in self._extract_urls(rec_res):
                    if ru not in urls:
                        urls.append(ru)
            # recommendで増えた分も read の上位に絞る
            urls = urls[:READ_TOP_K]

        # 3) read_documentation（上位 READ_TOP_K だけ）
        snippets: List[McpSnippet] = []
        for u in urls[:READ_TOP_K]:
            if len(snippets) >= max_snippets:
                break

            read_args = self._build_read_args(u)
            read_res = self._call_tool("aws___read_documentation", read_args)
            md_text = (self._extract_text(read_res) or "").strip()
            if not md_text:
                continue

            snippets.append(
                McpSnippet(
                    text=self._trim(md_text, SNIPPET_MAX_CHARS),
                    source="aws-knowledge-mcp",
                    url=u,
                )
            )

        return snippets

    # -------------------------
    # MCP plumbing
    # -------------------------

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return

        init_req = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": self._DEFAULT_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "aws-knowledge-quiz-app", "version": "1.0.0"},
            },
        }
        resp, headers = self._post_jsonrpc(init_req)

        pv = resp.get("result", {}).get("protocolVersion")
        if isinstance(pv, str) and pv:
            self._protocol_version = pv

        sid = headers.get("Mcp-Session-Id") or headers.get("mcp-session-id")
        if isinstance(sid, str) and sid:
            self._session_id = sid

        notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        self._post_jsonrpc(notif, expect_response=False)

        self._initialized = True

    def _call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        self._ensure_initialized()
        req = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        resp, _ = self._post_jsonrpc(req)
        return resp.get("result", {}) if isinstance(resp, dict) else {}

    def _post_jsonrpc(self, payload: Dict[str, Any], *, expect_response: bool = True) -> tuple[Dict[str, Any], Dict[str, str]]:
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": self._protocol_version,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if self.api_key:
            headers["x-api-key"] = self.api_key

        req = Request(self.endpoint, data=body, headers=headers, method="POST")

        try:
            with urlopen(req, timeout=self.timeout_seconds) as r:
                resp_headers = {k: v for (k, v) in r.headers.items()}
                if not expect_response:
                    return {}, resp_headers

                ctype = (r.headers.get("Content-Type") or "").lower()
                raw = r.read().decode("utf-8", errors="replace")

                if "application/json" in ctype:
                    return json.loads(raw) if raw else {}, resp_headers

                if "text/event-stream" in ctype:
                    obj = self._parse_sse_for_jsonrpc(raw, payload.get("id"))
                    return obj, resp_headers

                raise McpClientError(f"Unexpected Content-Type from MCP: {ctype}")

        except HTTPError as e:
            msg = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else str(e)
            raise McpClientError(f"MCP HTTPError: {e.code} {e.reason} {msg}") from e
        except URLError as e:
            raise McpClientError(f"MCP URLError: {e}") from e
        except Exception as e:
            raise McpClientError(f"MCP error: {e}") from e

    def _parse_sse_for_jsonrpc(self, sse_text: str, expected_id: Any) -> Dict[str, Any]:
        for line in sse_text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:") :].strip()
            if not data:
                continue
            try:
                obj = json.loads(data)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            if expected_id is None or obj.get("id") == expected_id:
                return obj
        return {}

    # -------------------------
    # Tool argument helpers
    # -------------------------

    def _build_search_args(self, query: str) -> Dict[str, Any]:
        return {
            "search_phrase": query,
            "searchPhrase": query,
            "query": query,
        }

    def _build_read_args(self, url: str) -> Dict[str, Any]:
        return {"url": url, "document_url": url, "documentUrl": url}

    def _build_recommend_args(self, url: str) -> Dict[str, Any]:
        return {"url": url, "document_url": url, "documentUrl": url}

    # -------------------------
    # Result parsers
    # -------------------------

    def _extract_text(self, tool_result: Dict[str, Any]) -> str:
        if tool_result.get("isError") is True:
            return ""

        sc = tool_result.get("structuredContent")
        if isinstance(sc, dict):
            for k in ("markdown", "text", "content"):
                v = sc.get(k)
                if isinstance(v, str) and v.strip():
                    return v

        content = tool_result.get("content", [])
        if not isinstance(content, list):
            return ""
        texts: List[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                t = item.get("text")
                if isinstance(t, str) and t:
                    texts.append(t)
        return "\n".join(texts).strip()

    def _extract_urls(self, tool_result: Dict[str, Any]) -> List[str]:
        if tool_result.get("isError") is True:
            return []

        urls: List[str] = []
        sc = tool_result.get("structuredContent")
        if isinstance(sc, dict):
            urls.extend(self._urls_from_any(sc))

        content = tool_result.get("content", [])
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") != "text":
                    continue
                t = item.get("text")
                if not isinstance(t, str) or not t.strip():
                    continue
                parsed = self._maybe_json(t)
                if parsed is not None:
                    urls.extend(self._urls_from_any(parsed))
                else:
                    for token in t.split():
                        if token.startswith("http://") or token.startswith("https://"):
                            urls.append(token)

        dedup: List[str] = []
        seen = set()
        for u in urls:
            if not isinstance(u, str):
                continue
            u = u.strip()
            if not u or u in seen:
                continue
            seen.add(u)
            dedup.append(u)
        return dedup

    def _urls_from_any(self, obj: Any) -> List[str]:
        urls: List[str] = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in ("url", "document_url", "documentUrl", "link") and isinstance(v, str):
                    urls.append(v)
                else:
                    urls.extend(self._urls_from_any(v))
        elif isinstance(obj, list):
            for x in obj:
                urls.extend(self._urls_from_any(x))
        elif isinstance(obj, str):
            if obj.startswith("http://") or obj.startswith("https://"):
                urls.append(obj)
        return urls

    def _maybe_json(self, text: str) -> Any | None:
        s = text.strip()
        if not s:
            return None
        if not (s.startswith("{") or s.startswith("[")):
            return None
        try:
            return json.loads(s)
        except Exception:
            return None

    # -------------------------
    # Utilities
    # -------------------------

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _trim(self, s: str, max_chars: int) -> str:
        if len(s) <= max_chars:
            return s
        return s[: max_chars - 1] + "…"
