from __future__ import annotations

import os
import json
import hashlib
from decimal import Decimal

import boto3
from urllib.parse import parse_qs

from common.bedrock import BedrockClient
from common.config import (
    BEDROCK_MODEL_ID,
    BEDROCK_PROMPT_ARN,
    BEDROCK_PROMPT_NAME,
    BEDROCK_GUARDRAIL_IDENTIFIER,
    BEDROCK_GUARDRAIL_VERSION,
    HOST_KEY,
    DUPLICATE_HINT_WINDOW,
    MAX_ATTEMPTS,
    MAX_MCP_REFRESH,
    MCP_API_KEY,
    MCP_ENDPOINT,
    QUIZ_TABLE_NAME,
    SOURCE_CONTEXT_MAX_CHARS,
    SOURCE_SNIPPETS_MAX,
)
from common.ddb import QuizRepo, now_iso_jst
from common.errors import AppError, ParseError, SchemaError, SemanticError
from common.mcp import McpClient
from common.normalize import question_hash
from common.schema import ALLOWED_CATEGORIES, ALLOWED_LEVELS, LIMITS
from common.validate import parse_json_strict

# Lambda client for async invocation
_lambda_client = boto3.client('lambda')

# Load JSON schema for structured output
_QUIZ_SCHEMA = None
_QUESTION_SCHEMA = None

def _load_quiz_schema() -> dict:
    global _QUIZ_SCHEMA
    if _QUIZ_SCHEMA is None:
        schema_path = os.path.join(os.path.dirname(__file__), "quiz_schema.json")
        with open(schema_path, "r", encoding="utf-8") as f:
            _QUIZ_SCHEMA = json.load(f)
    return _QUIZ_SCHEMA

def _load_question_schema() -> dict:
    """Load question-only schema (title + body + tags)"""
    global _QUESTION_SCHEMA
    if _QUESTION_SCHEMA is None:
        # Simplified schema for question generation
        _QUESTION_SCHEMA = {
            "type": "object",
            "properties": {
                "title": {"type": "string", "maxLength": 80},
                "body": {"type": "string", "maxLength": 300},
                "category": {"type": "string", "enum": list(ALLOWED_CATEGORIES)},
                "level": {"type": "integer", "enum": list(ALLOWED_LEVELS)},
                "sourceSummary": {"type": "string", "maxLength": 250},
                "tags": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["title", "body", "category", "level", "sourceSummary", "tags"],
            "additionalProperties": False
        }
    return _QUESTION_SCHEMA


def _validate_question(obj: dict) -> dict:
    """Validate question-only generation (without rubric)"""
    from common.validate import _require_str, _require_int, _is_list, _is_str, _sanitize_text, _strip
    
    title = _require_str(obj, "title", min_len=1, max_len=LIMITS["title_max"])
    body = _require_str(obj, "body", min_len=1, max_len=LIMITS["body_max"])
    
    category = _require_str(obj, "category", min_len=1, max_len=32)
    if category not in ALLOWED_CATEGORIES:
        raise SchemaError(f"category not allowed: {category}")
    
    level = _require_int(obj, "level")
    if level not in ALLOWED_LEVELS:
        raise SchemaError(f"level not allowed: {level}")
    
    source_summary = _require_str(obj, "sourceSummary", min_len=1, max_len=LIMITS["source_summary_max"])
    
    tags_raw = obj.get("tags", [])
    if tags_raw is None:
        tags_raw = []
    if not _is_list(tags_raw):
        raise SchemaError("tags must be list")
    if len(tags_raw) > 20:
        raise SchemaError("tags too many items (max 20)")
    
    tags = []
    for i, t in enumerate(tags_raw):
        if not _is_str(t):
            raise SchemaError(f"tags[{i}] must be string")
        t2 = _sanitize_text(_strip(t))
        if not t2:
            raise SchemaError(f"tags[{i}] empty")
        if len(t2) > 40:
            raise SchemaError(f"tags[{i}] too long (max 40)")
        tags.append(t2)
    
    return {
        "title": title,
        "body": body,
        "category": category,
        "level": level,
        "sourceSummary": source_summary,
        "tags": tags,
    }

# -----------------------------
# MCP query components (fast + deterministic diversification)
# -----------------------------

QUERY_COMPONENTS: dict[str, dict[str, list[str]]] = {
    "security": {
        "services": [
            "IAM",
            "STS AssumeRole",
            "Organizations SCP",
            "KMS",
            "S3",
            "CloudTrail",
            "Config",
            "GuardDuty",
            "WAF",
            "Secrets Manager",
        ],
        "topics": [
            "最小権限",
            "評価ロジック",
            "境界",
            "条件キー",
            "監査",
            "誤設定",
            "マルチアカウント",
            "暗号化",
            "アクセス制御",
        ],
        "angles": [
            "ベストプラクティス",
            "誤解",
            "設計",
            "運用",
            "トレードオフ",
        ],
    },
    "networking": {
        "services": [
            "VPC",
            "Route Table",
            "NAT Gateway",
            "Internet Gateway",
            "VPC Endpoint",
            "Transit Gateway",
            "Direct Connect",
            "Site-to-Site VPN",
            "ALB",
            "NLB",
            "CloudFront",
            "API Gateway",
        ],
        "topics": [
            "ルーティング",
            "DNS",
            "到達性",
            "セキュリティグループ",
            "ハイブリッド接続",
            "L4/L7",
            "可用性",
            "コスト",
        ],
        "angles": [
            "基礎",
            "使い分け",
            "設計",
            "障害",
            "アンチパターン",
        ],
    },
    "storage": {
        "services": [
            "S3",
            "EBS",
            "EFS",
            "FSx",
            "DataSync",
            "Storage Gateway",
            "Glacier",
            "S3 Object Lock",
        ],
        "topics": [
            "耐久性",
            "ライフサイクル",
            "暗号化",
            "バックアップ",
            "性能",
            "コスト最適化",
            "整合性",
            "アクセス制御",
        ],
        "angles": [
            "仕組み",
            "注意点",
            "使い分け",
            "運用",
            "監査",
        ],
    },
    "serverless": {
        "services": [
            "Lambda",
            "API Gateway",
            "EventBridge",
            "SQS",
            "SNS",
            "Step Functions",
            "DynamoDB",
            "AppSync",
            "Amplify",
            "Fargate",
        ],
        "topics": [
            "非同期",
            "再試行",
            "同時実行",
            "DLQ",
            "オーケストレーション",
            "イベント駆動",
            "権限",
            "監視",
            "データモデリング",
        ],
        "angles": [
            "ベストプラクティス",
            "トラブルシュート",
            "設計パターン",
            "制限",
            "アンチパターン",
        ],
    },
    "well-architected": {
        "services": [
            "Well-Architected Framework",
            "運用上の優秀性",
            "セキュリティ",
            "信頼性",
            "パフォーマンス効率",
            "コスト最適化",
            "サステナビリティ",
        ],
        "topics": [
            "柱の要点",
            "設計原則",
            "ベストプラクティス",
            "落とし穴",
            "トレードオフ",
            "可観測性",
        ],
        "angles": [
            "要点",
            "具体例",
            "誤解",
            "改善",
            "レビュー",
        ],
    },
}

QUESTION_STYLES: list[tuple[str, str]] = [
    ("使い分け", "A/B/Cの違いを『要件→選定理由→注意点』で問う。"),
    ("トレードオフ", "条件次第で変わる論点を出す。何を捨て何を取るかを答えさせる。"),
    ("誤り探し", "設定/設計のどこが危険か、どう直すべきかを問う。"),
    ("運用", "事象→原因→切り分け→対策を答えさせる。"),
    ("監査", "監査で指摘されやすい点を盛り込む。"),
    ("コスト", "コストの増えやすいポイントと下げる方法を問う。"),
]


def _level_suffix(level: int) -> str:
    """Simplified level suffix for faster MCP search"""
    return {
        100: "AWS初心者向け。単一サービスの基本機能や用語を問う。",
        200: "実務経験3年程度。サービスの設定や使い分けを問う。",
        300: "実務経験7年程度。複数サービスの連携や制限事項を問う。",
        400: "リードアーキテクトや上級者向け。障害対応、アーキテクチャ判断、トレードオフを問う。",
    }[level]


def _stable_pick(seed: str, n: int) -> int:
    if n <= 0:
        return 0
    h = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    v = int(h[:12], 16)
    return v % n


def _query_space_size(category: str) -> int:
    c = QUERY_COMPONENTS[category]
    return max(1, len(c["services"]) * len(c["topics"]) * len(c["angles"]))


def _build_mcp_query_and_style(category: str, level: int, idx: int) -> tuple[str, str, str]:
    """Simplified MCP query for faster search"""
    c = QUERY_COMPONENTS[category]
    services = c["services"]
    topics = c["topics"]
    angles = c["angles"]

    space = _query_space_size(category)
    i = idx % space

    s_idx = i % len(services)
    t_idx = (i // len(services)) % len(topics)
    a_idx = (i // (len(services) * len(topics))) % len(angles)

    service = services[s_idx]
    topic = topics[t_idx]
    angle = angles[a_idx]
    lvl = _level_suffix(level)

    # Simplified query - faster search
    query = f"{service} {topic} {lvl}".strip()

    st_i = _stable_pick(f"{category}:{level}:{idx}:style", len(QUESTION_STYLES))
    style_name, style_guidance = QUESTION_STYLES[st_i]

    return query, style_name, style_guidance


# -----------------------------
# Query rotation cursor (DynamoDB)
# -----------------------------

_DDB = boto3.resource("dynamodb")


def _level_bucket(level: int) -> str:
    return f"b{max(0, int(level) // 200)}"


def _cursor_pk(category: str, level: int) -> str:
    return f"CURSOR#{category}#{_level_bucket(level)}"


def _get_cursor_next_idx(table_name: str, category: str, level: int) -> int:
    table = _DDB.Table(table_name)
    pk = _cursor_pk(category, level)
    resp = table.get_item(Key={"QuestionHash": pk}, ConsistentRead=True)
    item = resp.get("Item") or {}
    try:
        return int(item.get("NextIdx", 0))
    except Exception:
        return 0


def _advance_cursor_next_idx(
    table_name: str, category: str, level: int, expected_old: int, new_value: int
) -> None:
    table = _DDB.Table(table_name)
    pk = _cursor_pk(category, level)

    try:
        table.update_item(
            Key={"QuestionHash": pk},
            UpdateExpression="SET NextIdx = :new, ItemType = :t, UpdatedAt = :u",
            ConditionExpression="attribute_not_exists(NextIdx) OR NextIdx = :old",
            ExpressionAttributeValues={
                ":new": int(new_value),
                ":old": int(expected_old),
                ":t": "cursor",
                ":u": now_iso_jst(),
            },
        )
    except Exception:
        return


# -----------------------------
# Helpers
# -----------------------------

def _make_avoid_hint(h: dict) -> str:
    """Simplified duplicate avoidance hint"""
    items = []
    if h.get("recentTitles"):
        items.extend(h["recentTitles"][:2])
    if h.get("recentTags"):
        items.extend(h["recentTags"][:2])
    
    if items:
        return "最近: " + ", ".join(items) + "\n→別の観点で作問\n"
    return ""


def _build_source_context(snippets: list[str]) -> str:
    lines = [f"AWS資料:"]
    for i, t in enumerate(snippets[:SOURCE_SNIPPETS_MAX], start=1):
        t2 = t.strip()
        if len(t2) > 400:
            t2 = t2[:400] + "…"
        lines.append(f"[{i}] {t2}")
    text = "\n".join(lines)
    return text[:SOURCE_CONTEXT_MAX_CHARS]


def _sanitize_for_hash(s: str) -> str:
    if s is None:
        return ""
    if not isinstance(s, str):
        s = str(s)
    s2 = "".join(ch for ch in s if 32 <= ord(ch) <= 126 or ord(ch) >= 160)
    s2 = " ".join(s2.split())
    return s2


def _resp(status: int, body: dict, event: dict | None = None):
    origin = None
    if isinstance(event, dict):
        headers = event.get("headers") or {}
        origin = headers.get("origin") or headers.get("Origin")

    cors_origin = origin or "*"

    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json; charset=utf-8",
            "access-control-allow-origin": cors_origin,
            "access-control-allow-methods": "GET,OPTIONS",
            "access-control-allow-headers": "content-type,authorization",
            "access-control-allow-credentials": "true",
            "vary": "origin",
        },
        "body": json.dumps(body, ensure_ascii=False),
    }


def _to_ddb_safe(x):
    if isinstance(x, float):
        return Decimal(str(x))
    if isinstance(x, dict):
        return {k: _to_ddb_safe(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_to_ddb_safe(v) for v in x]
    return x


def _mask(s: str | None, keep: int = 6) -> str:
    if not s:
        return ""
    s = str(s)
    if len(s) <= keep:
        return "*" * len(s)
    return s[:keep] + "..."


def _resolve_effective_prompt_arn(bedrock: BedrockClient, request_id: str, prompt_type: str = "question") -> str:
    """
    Resolve effective prompt ARN for question or rubric generation
    
    Args:
        bedrock: BedrockClient instance
        request_id: Request ID for logging
        prompt_type: "question" or "rubric" or "full" (legacy)
    """
    if prompt_type == "question":
        env_key = "BEDROCK_QUESTION_PROMPT_ARN"
    elif prompt_type == "rubric":
        env_key = "BEDROCK_RUBRIC_PROMPT_ARN"
    else:  # "full" or legacy
        env_key = "BEDROCK_PROMPT_ARN"
    
    env_prompt_arn = os.environ.get(env_key, "").strip()
    env_prompt_name = (BEDROCK_PROMPT_NAME or "").strip()

    print(
        f"[CFG] {prompt_type} prompt config",
        {
            "requestId": request_id,
            f"{env_key}": _mask(env_prompt_arn, 18),
            "BEDROCK_PROMPT_NAME": env_prompt_name,
        },
    )

    if env_prompt_arn:
        return env_prompt_arn

    if not env_prompt_name:
        raise AppError(
            "ConfigError",
            f"Missing {env_key} (and BEDROCK_PROMPT_NAME is empty)",
            500,
        )

    try:
        resolved = bedrock.resolve_latest_prompt_version_arn(prompt_name=env_prompt_name)
        print(
            f"[INFO] resolved latest {prompt_type} prompt version arn",
            {"requestId": request_id, "promptArn": _mask(resolved, 18)},
        )
        return resolved
    except Exception as ex:
        print(
            f"[ERROR] failed to resolve latest {prompt_type} prompt version arn",
            {"requestId": request_id, "error": repr(ex)},
        )
        raise AppError(
            "ConfigError",
            f"Missing {env_key} (and failed to resolve from BEDROCK_PROMPT_NAME)",
            500,
        )


import re

_JSON_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*([\s\S]*?)\s*```\s*$", re.IGNORECASE)


def _rescue_json_text(raw: str) -> tuple[str, str | None]:
    if not isinstance(raw, str):
        return raw, None

    s = raw.strip()

    if s.startswith("```"):
        first_newline = s.find("\n")
        if first_newline != -1:
            closing = s.rfind("```")
            if closing > first_newline:
                content = s[first_newline + 1:closing].strip()
                if content.startswith("{") or content.startswith("["):
                    return content, "stripped_code_fence"

    m = _JSON_FENCE_RE.match(s)
    if m:
        return m.group(1).strip(), "stripped_code_fence_whole"

    if "```" in s:
        blocks = re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", s, flags=re.IGNORECASE)
        if blocks:
            candidate = blocks[0].strip()
            if candidate.startswith("{") or candidate.startswith("["):
                return candidate, "extracted_code_fence_block"

    first_obj = s.find("{")
    last_obj = s.rfind("}")
    if first_obj != -1 and last_obj != -1 and first_obj < last_obj:
        candidate = s[first_obj: last_obj + 1].strip()
        if candidate.startswith("{") and candidate.endswith("}"):
            return candidate, "extracted_outer_object"

    first_arr = s.find("[")
    last_arr = s.rfind("]")
    if first_arr != -1 and last_arr != -1 and first_arr < last_arr:
        candidate = s[first_arr: last_arr + 1].strip()
        if candidate.startswith("[") and candidate.endswith("]"):
            return candidate, "extracted_outer_array"

    return raw, None


# -----------------------------
# Lambda handler
# -----------------------------

def lambda_handler(event, context):
    try:
        method = (event.get("requestContext", {}).get("http", {}).get("method") or "").upper()
        if method == "OPTIONS":
            return _resp(200, {"ok": True}, event)

        expected = (HOST_KEY or "").strip()
        if expected and not expected.startswith("CHANGE_ME"):
            headers = event.get("headers") or {}
            got = headers.get("x-host-key") or headers.get("X-Host-Key")
            if not got or str(got).strip() != expected:
                raise AppError("Forbidden", "Host key is required", 403)

        aws_request_id = getattr(context, "aws_request_id", "-")

        print(
            "[CFG] effective runtime config",
            {
                "requestId": aws_request_id,
                "MAX_ATTEMPTS": MAX_ATTEMPTS,
                "MAX_MCP_REFRESH": MAX_MCP_REFRESH,
                "SOURCE_SNIPPETS_MAX": SOURCE_SNIPPETS_MAX,
                "SOURCE_CONTEXT_MAX_CHARS": SOURCE_CONTEXT_MAX_CHARS,
            },
        )

        qs = parse_qs((event.get("rawQueryString") or ""))
        category = (qs.get("category", [None])[0] or "security").strip()
        level_str = (qs.get("level", [None])[0] or "100").strip()

        print("[INFO] start get_next_quiz", {"requestId": aws_request_id, "category": category, "level": level_str})

        if category not in ALLOWED_CATEGORIES:
            raise AppError("BadRequest", f"Invalid category: {category}", 400)

        try:
            level = int(level_str)
        except ValueError:
            raise AppError("BadRequest", f"Invalid level: {level_str}", 400)

        if level not in ALLOWED_LEVELS:
            raise AppError("BadRequest", f"Invalid level: {level}", 400)

        repo = QuizRepo(QUIZ_TABLE_NAME)

        brt = boto3.client("bedrock-runtime")
        bedrock = BedrockClient(
            brt,
            BEDROCK_MODEL_ID,
            guardrail_identifier=BEDROCK_GUARDRAIL_IDENTIFIER if BEDROCK_GUARDRAIL_IDENTIFIER else None,
            guardrail_version=BEDROCK_GUARDRAIL_VERSION if BEDROCK_GUARDRAIL_IDENTIFIER else None,
        )

        mcp = McpClient(MCP_ENDPOINT, MCP_API_KEY)

        effective_prompt_arn = _resolve_effective_prompt_arn(bedrock, aws_request_id, "question")

        hints = repo.get_recent_hints(DUPLICATE_HINT_WINDOW)
        avoid_hint = _make_avoid_hint(hints)

        start_idx = _get_cursor_next_idx(QUIZ_TABLE_NAME, category, level)

        space = _query_space_size(category)
        max_refresh = min(MAX_MCP_REFRESH, max(0, space - 1))

        refresh = 0
        while refresh <= max_refresh:
            q_idx = start_idx + refresh
            query, style_name, style_guidance = _build_mcp_query_and_style(category, level, q_idx)

            print(
                "[INFO] MCP search",
                {"requestId": aws_request_id, "refresh": refresh, "query": query, "style": style_name},
            )

            mcp_snippets = mcp.search(query=query, max_snippets=SOURCE_SNIPPETS_MAX)

            source_context = _build_source_context([s.text for s in mcp_snippets])

            broke_for_time = False
            for attempt in range(1, MAX_ATTEMPTS + 1):
                remaining_ms = context.get_remaining_time_in_millis()
                if remaining_ms < 35000:
                    print(
                        "[WARN] Not enough time remaining; stop generation loop",
                        {"requestId": aws_request_id, "remainingMs": remaining_ms, "refresh": refresh, "attempt": attempt},
                    )
                    broke_for_time = True
                    break

                prompt_vars = {
                    "category": str(category),
                    "level": str(level),
                    "avoid_duplicate_hint": str(avoid_hint),
                    "question_style": str(style_name),
                    "style_guidance": str(style_guidance),
                    "source_context": str(source_context),
                }

                # Generate question only (title + body + tags)
                raw = bedrock.converse_prompt_json(
                    prompt_arn=effective_prompt_arn,
                    prompt_variables=prompt_vars,
                )

                raw_len = len(raw) if isinstance(raw, str) else -1
                print(
                    "[INFO] Bedrock returned question (title+body)",
                    {"requestId": aws_request_id, "refresh": refresh, "attempt": attempt, "rawLen": raw_len},
                )

                if isinstance(raw, str):
                    try:
                        raw_obj = json.loads(raw)
                        if isinstance(raw_obj, dict) and raw_obj.get("error") == "guardrail_blocked":
                            print(
                                "[WARN] Guardrail blocked content generation",
                                {"requestId": aws_request_id, "refresh": refresh, "attempt": attempt},
                            )
                            break
                    except json.JSONDecodeError:
                        pass

                try:
                    cleaned, reason = _rescue_json_text(raw if isinstance(raw, str) else "")
                    if reason:
                        print(
                            "[INFO] rescued json text",
                            {
                                "requestId": aws_request_id,
                                "refresh": refresh,
                                "attempt": attempt,
                                "reason": reason,
                            },
                        )
                    obj = parse_json_strict(cleaned if reason else raw, LIMITS["raw_json_max"])
                    question = _validate_question(obj)  # Validate question only
                except (ParseError, SchemaError, SemanticError) as e:
                    print(
                        f"[WARN] generation failed ({e.code}): {e.message}",
                        {"requestId": aws_request_id, "refresh": refresh, "attempt": attempt},
                    )
                    if isinstance(raw, str):
                        print(f"[WARN] raw(head): {raw[:300]}")
                    continue

                safe_title = _sanitize_for_hash(question["title"])
                safe_body = _sanitize_for_hash(question["body"])
                
                # Generate temporary hash without rubric (will be same as final hash since rubric not used in hash)
                qhash = question_hash(
                    category=question["category"],
                    level=int(question["level"]),
                    title=safe_title,
                    body=safe_body,
                    must_points=[],  # No rubric yet
                )

                created_at = now_iso_jst()

                item = {
                    "QuestionHash": qhash,
                    "GSI1PK": "RECENT",
                    "GSI1SK": created_at,
                    "Version": 1,
                    "Category": question["category"],
                    "Level": int(question["level"]),
                    "Language": "ja",
                    "Question": {"Title": question["title"], "Body": question["body"]},
                    # Rubric will be added later by GenerateRubricFunction
                    "SourceContext": {
                        "Provider": "aws-knowledge-mcp",
                        "RetrievedAt": created_at,
                        "Snippets": [
                            {
                                "id": f"s{i}",
                                "text": s.text,
                                "source": s.source or "AWS (via MCP)",
                                "url": s.url,
                                "title": s.title,
                            }
                            for i, s in enumerate(mcp_snippets[:SOURCE_SNIPPETS_MAX], start=1)
                        ],
                    },
                    "CreatedAt": created_at,
                    "Tags": question["tags"],
                }

                item = _to_ddb_safe(item)

                ok = repo.put_unique(item)
                print(
                    "[DDB] put_unique",
                    {
                        "requestId": aws_request_id,
                        "ok": ok,
                        "qhash": qhash,
                        "refresh": refresh,
                        "attempt": attempt,
                    },
                )

                _advance_cursor_next_idx(
                    QUIZ_TABLE_NAME,
                    category,
                    level,
                    expected_old=q_idx,
                    new_value=(q_idx + 1),
                )

                if ok:
                    # Invoke GenerateRubricFunction asynchronously
                    generate_rubric_function = os.environ.get('GENERATE_RUBRIC_FUNCTION_NAME')
                    if generate_rubric_function:
                        try:
                            rubric_payload = {
                                "questionHash": qhash,
                                "category": question["category"],
                                "level": question["level"],
                                "title": question["title"],
                                "body": question["body"],
                                "sourceContext": source_context,
                                "requestId": aws_request_id,
                            }
                            _lambda_client.invoke(
                                FunctionName=generate_rubric_function,
                                InvocationType='Event',  # Async
                                Payload=json.dumps(rubric_payload).encode('utf-8')
                            )
                            print(
                                "[INFO] Started async rubric generation",
                                {"requestId": aws_request_id, "questionHash": qhash},
                            )
                        except Exception as e:
                            print(
                                "[ERROR] Failed to invoke rubric generation",
                                {"requestId": aws_request_id, "error": repr(e)},
                            )
                            # Continue anyway - rubric can be generated on-demand during judging
                    
                    return _resp(
                        200,
                        {
                            "question": {
                                "questionId": qhash,
                                "title": item["Question"]["Title"],
                                "body": item["Question"]["Body"],
                                "category": item["Category"],
                                "level": item["Level"],
                                "createdAt": item["CreatedAt"],
                            }
                        },
                        event,
                    )

                return _resp(
                    503,
                    {
                        "error": {
                            "code": "DuplicateQuizGenerated",
                            "message": "重複したクイズが生成されたため保存できませんでした。もう一度お試しください。",
                        }
                    },
                    event,
                )

            if broke_for_time:
                break

            refresh += 1

        return _resp(
            503,
            {"error": {"code": "NoUniqueQuizAvailable", "message": "一意なクイズを生成できませんでした。カテゴリまたは難易度を変更してください。"}},
            event,
        )

    except AppError as e:
        return _resp(e.status_code, {"error": {"code": e.code, "message": e.message}}, event)
    except (ParseError, SchemaError, SemanticError) as e:
        return _resp(e.status_code, {"error": {"code": e.code, "message": e.message}}, event)
    except Exception as e:
        print("[ERROR] Unexpected exception in get_next_quiz")
        print("[ERROR] repr:", repr(e))
        import traceback
        traceback.print_exc()
        return _resp(500, {"error": {"code": "InternalError", "message": "Unexpected error"}}, event)
