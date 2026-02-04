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
from common.validate import parse_json_strict, validate_generation

# -----------------------------
# MCP query components (fast + deterministic diversification)
# -----------------------------

# NOTE:
# - 既存の「固定クエリ配列」を増やすとメンテ負荷が上がりやすいので、
#   “部品の組み合わせ”でクエリを生成する方式に置換。
# - 生成は決定的（category/level/idx からハッシュで選ぶ）なので、DDBカーソルと相性が良い。
# - MCP search 回数は増やさない（refresh ループはそのまま）。
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
            "境界(permissions boundary)",
            "条件キー",
            "監査・証跡",
            "誤設定パターン",
            "マルチアカウント",
            "暗号化と鍵管理",
            "アクセス制御",
        ],
        "angles": [
            "ベストプラクティス",
            "よくある誤解",
            "設計の注意点",
            "運用上の落とし穴",
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
            "名前解決(DNS)",
            "到達性(Reachability)",
            "セキュリティグループとNACL",
            "ハイブリッド接続",
            "L4/L7の使い分け",
            "可用性設計",
            "コストと性能",
        ],
        "angles": [
            "基礎",
            "使い分け",
            "設計の観点",
            "障害・切り分け",
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
            "耐久性と可用性",
            "ライフサイクル",
            "暗号化",
            "バックアップ/復元",
            "性能(スループット/IOPS)",
            "コスト最適化",
            "整合性とバージョニング",
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
            "非同期/同期",
            "再試行と冪等性",
            "同時実行とスロットリング",
            "DLQ/宛先",
            "オーケストレーション",
            "イベント駆動設計",
            "権限/IAM",
            "監視と運用",
            "データモデリング(GSI/LSI)",
        ],
        "angles": [
            "ベストプラクティス",
            "トラブルシュート",
            "設計パターン",
            "制限・クォータ",
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
            "代表的なベストプラクティス",
            "よくある落とし穴",
            "トレードオフ",
            "メトリクス/可観測性",
        ],
        "angles": [
            "要点整理",
            "具体例",
            "誤解の修正",
            "改善アクション",
            "レビュー観点",
        ],
    },
}

QUESTION_STYLES: list[tuple[str, str]] = [
    ("使い分け", "A/B/Cの違いを『要件→選定理由→注意点』で問う。単なる定義暗記にしない。"),
    ("トレードオフ", "正解が一つに見えても条件次第で変わる論点を出す。何を捨て何を取るかを答えさせる。"),
    ("誤り探し", "提示した設定/設計のどこが危険か、どう直すべきかを問う。"),
    ("運用・障害対応", "事象→原因候補→切り分け手順→恒久対策を答えさせる。"),
    ("監査・コンプラ観点", "監査で指摘されやすい点、証跡/責任分界/統制の観点を盛り込む。"),
    ("コスト最適化", "コストの増えやすいポイントと、要件を満たしつつ下げる方法を問う。"),
]


def _level_suffix(level: int) -> str:
    return {
        100: "入門 基礎 概要",
        200: "ベストプラクティス 設計 推奨 機能の詳細",
        300: "設定 運用 実装 トラブルシュート 注意点",
        400: "設計トレードオフ 複数サービスやアーキテクチャによる実装 アンチパターン 深掘り",
    }[level]


def _stable_pick(seed: str, n: int) -> int:
    """
    0..n-1 を決定的に選ぶ（ランダムではなく、同じseedなら同じ結果）。
    DDBカーソルでのローテーションと相性が良い。
    """
    if n <= 0:
        return 0
    h = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    v = int(h[:12], 16)  # enough
    return v % n


def _query_space_size(category: str) -> int:
    c = QUERY_COMPONENTS[category]
    # “サービス×トピック×角度” の組み合わせ空間
    return max(1, len(c["services"]) * len(c["topics"]) * len(c["angles"]))


def _build_mcp_query_and_style(category: str, level: int, idx: int) -> tuple[str, str, str]:
    """
    idx を組み合わせ空間に写像して MCP 検索クエリを作る。
    MCP 検索クエリ自体は過度に振らず、ヒット品質を維持しつつバリエーションを増やす。
    併せて Bedrock へ渡す QUESTION_STYLE をローテする。
    """
    c = QUERY_COMPONENTS[category]
    services = c["services"]
    topics = c["topics"]
    angles = c["angles"]

    space = _query_space_size(category)
    i = idx % space

    # 3次元インデックスへ展開
    s_idx = i % len(services)
    t_idx = (i // len(services)) % len(topics)
    a_idx = (i // (len(services) * len(topics))) % len(angles)

    service = services[s_idx]
    topic = topics[t_idx]
    angle = angles[a_idx]

    # レベル別の補助語（既存の _level_suffix を踏襲）
    lvl = _level_suffix(level)

    # ちょい足しの“ゆらぎ”を決定的に付与（検索回数は増やさず、ヒット分布を少し変える）
    # 付けすぎると精度低下するので少数に限定
    micro_mods = ["注意点", "制限", "設計", "運用", "ベストプラクティス", "よくある誤解"]
    m_idx = _stable_pick(f"{category}:{level}:{idx}:micro", len(micro_mods))
    micro = micro_mods[m_idx]

    # MCP query（短く・意図が通る形）
    query = f"{service} {topic} {angle} {micro} {lvl}".strip()

    # Question style (Bedrock側の出題“型”)
    st_i = _stable_pick(f"{category}:{level}:{idx}:style", len(QUESTION_STYLES))
    style_name, style_guidance = QUESTION_STYLES[st_i]

    return query, style_name, style_guidance


# -----------------------------
# Query rotation cursor (DynamoDB)
# -----------------------------

_DDB = boto3.resource("dynamodb")


def _level_bucket(level: int) -> str:
    # 200-point bands: 0-199 -> b0, 200-399 -> b1, ...
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
    """Advance cursor only if it still has the expected value.

    This keeps writes safe under concurrent callers while still updating only on success.
    """
    table = _DDB.Table(table_name)
    pk = _cursor_pk(category, level)

    # We also set ItemType so operators can identify these items easily.
    # IMPORTANT: do NOT set GSI1PK/GSI1SK so this never appears in GSI_Recent.
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
        # Best-effort: if a concurrent writer already advanced it, that's OK.
        # The next request will read the latest cursor value.
        return


# -----------------------------
# Helpers
# -----------------------------

def _make_avoid_hint(h: dict) -> str:
    def lines(title: str, items: list[str], max_items: int) -> str:
        items = items[:max_items]
        if not items:
            return f"{title}:\n- (none)\n"
        return title + ":\n" + "\n".join([f"- {x}" for x in items]) + "\n"

    s = ""
    s += lines("RECENT_TITLES", h.get("recentTitles", []), 5)
    s += "\n" + lines("RECENT_TAGS", h.get("recentTags", []), 6)
    s += "\n" + lines("RECENT_MUST_POINTS", h.get("recentMustLabels", []), 8)
    s += "\nNOTE:\n- 直近と同じ論点や言い回しを避け、別の観点で作問してください。\n"
    return s


def _build_source_context(snippets: list[str]) -> str:
    lines = [f"SOURCE_SNIPPETS (max {SOURCE_SNIPPETS_MAX}):"]
    for i, t in enumerate(snippets[:SOURCE_SNIPPETS_MAX], start=1):
        t2 = t.strip()
        if len(t2) > 650:
            t2 = t2[:650] + "…"
        lines.append(f"[{i}] {t2}")
    lines.append("")
    lines.append("KEY_TERMS:")
    lines.append("- (auto)")
    text = "\n".join(lines)
    return text[:SOURCE_CONTEXT_MAX_CHARS]


def _sanitize_for_hash(s: str) -> str:
    """
    question_hash 用の強制サニタイズ。
    - 制御文字をすべて除去（改行/タブ含む）
    - 連続空白を1つに圧縮
    """
    if s is None:
        return ""
    if not isinstance(s, str):
        s = str(s)
    s2 = "".join(ch for ch in s if 32 <= ord(ch) <= 126 or ord(ch) >= 160)
    s2 = " ".join(s2.split())
    return s2


def _resp(status: int, body: dict, event: dict | None = None):
    # CORS: reflect Origin if present
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
    """
    DynamoDBに入れる前に float を Decimal に変換する（再帰）。
    boto3 dynamodb は float 非対応。
    """
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


def _resolve_effective_prompt_arn(bedrock: BedrockClient, request_id: str) -> str:
    """
    Resolve prompt arn once per invocation.

    Priority:
      1) BEDROCK_PROMPT_ARN (env) if set
      2) Resolve by BEDROCK_PROMPT_NAME (env) via bedrock-agent
    """
    env_prompt_arn = (BEDROCK_PROMPT_ARN or "").strip()
    env_prompt_name = (BEDROCK_PROMPT_NAME or "").strip()

    # extra: allow override by raw env (helps debug if config layer mismatches)
    if not env_prompt_arn:
        env_prompt_arn = (os.environ.get("BEDROCK_PROMPT_ARN") or "").strip()
    if not env_prompt_name:
        env_prompt_name = (os.environ.get("BEDROCK_PROMPT_NAME") or "").strip()

    print(
        "[CFG] prompt config",
        {
            "requestId": request_id,
            "BEDROCK_PROMPT_ARN": _mask(env_prompt_arn, 18),
            "BEDROCK_PROMPT_NAME": env_prompt_name,
        },
    )

    if env_prompt_arn:
        return env_prompt_arn

    if not env_prompt_name:
        raise AppError(
            "ConfigError",
            "Missing BEDROCK_PROMPT_ARN (and BEDROCK_PROMPT_NAME is empty)",
            500,
        )

    try:
        resolved = bedrock.resolve_latest_prompt_version_arn(prompt_name=env_prompt_name)
        print(
            "[INFO] resolved latest prompt version arn",
            {"requestId": request_id, "promptArn": _mask(resolved, 18)},
        )
        return resolved
    except Exception as ex:
        print(
            "[ERROR] failed to resolve latest prompt version arn",
            {"requestId": request_id, "error": repr(ex)},
        )
        raise AppError(
            "ConfigError",
            "Missing BEDROCK_PROMPT_ARN (and failed to resolve from BEDROCK_PROMPT_NAME)",
            500,
        )


# フェンス救済関数
import re

_JSON_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*([\s\S]*?)\s*```\s*$", re.IGNORECASE)


def _rescue_json_text(raw: str) -> tuple[str, str | None]:
    """
    Try to rescue JSON text from common LLM formatting issues.
    Returns (cleaned_text, reason) where reason is None if no rescue applied.
    """
    if not isinstance(raw, str):
        return raw, None

    s = raw.strip()

    # 1) Whole response is fenced ```json ... ```
    m = _JSON_FENCE_RE.match(s)
    if m:
        return m.group(1).strip(), "stripped_code_fence_whole"

    # 2) Response contains a fenced block somewhere; extract first fenced block
    if "```" in s:
        # Try to find the first fenced block (json or not)
        # e.g. "blah\n```json\n{...}\n```\nblah"
        blocks = re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", s, flags=re.IGNORECASE)
        if blocks:
            candidate = blocks[0].strip()
            # If candidate looks like JSON, return it
            if candidate.startswith("{") or candidate.startswith("["):
                return candidate, "extracted_code_fence_block"

    # 3) As a last resort, extract outermost {...} or [...] region if it looks plausible
    # (Keep conservative to avoid accidental truncation)
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
        # ---- preflight ----
        method = (event.get("requestContext", {}).get("http", {}).get("method") or "").upper()
        if method == "OPTIONS":
            return _resp(200, {"ok": True}, event)

        # ---- host guard ----
        # This endpoint advances the quiz for everyone. Guard it with a shared secret.
        expected = (HOST_KEY or "").strip()
        if expected and not expected.startswith("CHANGE_ME"):
            headers = event.get("headers") or {}
            # HTTP API v2 normalizes header keys to lower-case.
            got = headers.get("x-host-key") or headers.get("X-Host-Key")
            if not got or str(got).strip() != expected:
                raise AppError("Forbidden", "Host key is required", 403)

        aws_request_id = getattr(context, "aws_request_id", "-")

        # === runtime config dump (to debug double generation) ===
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

        # ---- request params ----
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

        # ---- clients ----
        repo = QuizRepo(QUIZ_TABLE_NAME)

        brt = boto3.client("bedrock-runtime")
        bedrock = BedrockClient(
            brt,
            BEDROCK_MODEL_ID,
            guardrail_identifier=BEDROCK_GUARDRAIL_IDENTIFIER if BEDROCK_GUARDRAIL_IDENTIFIER else None,
            guardrail_version=BEDROCK_GUARDRAIL_VERSION if BEDROCK_GUARDRAIL_IDENTIFIER else None,
        )

        mcp = McpClient(MCP_ENDPOINT, MCP_API_KEY)

        # ★ Resolve prompt ARN once per invocation (avoid conflicting checks / repeated lookups)
        effective_prompt_arn = _resolve_effective_prompt_arn(bedrock, aws_request_id)

        # ---- duplicate-avoid hints from DDB (GSI_Recent) ----
        hints = repo.get_recent_hints(DUPLICATE_HINT_WINDOW)
        avoid_hint = _make_avoid_hint(hints)

        # ---- MCP refresh loop (query rotation) ----
        # cursor is kept, but we rotate over a large deterministic combination space
        start_idx = _get_cursor_next_idx(QUIZ_TABLE_NAME, category, level)

        space = _query_space_size(category)
        # Keep same idea as before: refresh tries "nearby" candidates (bounded by MAX_MCP_REFRESH)
        # Use space to avoid refresh > space-1 when space is small (still usually large).
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
            snippets = [s.text for s in mcp_snippets]

            source_context = _build_source_context(snippets)

            # ---- generation attempts loop ----
            broke_for_time = False
            for attempt in range(1, MAX_ATTEMPTS + 1):
                remaining_ms = context.get_remaining_time_in_millis()
                if remaining_ms < 12000:
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

                raw = bedrock.converse_prompt_json(
                    prompt_arn=effective_prompt_arn,
                    prompt_variables=prompt_vars,
                )

                raw_len = len(raw) if isinstance(raw, str) else -1
                print(
                    "[INFO] Bedrock returned",
                    {"requestId": aws_request_id, "refresh": refresh, "attempt": attempt, "rawLen": raw_len},
                )

                # guardrailブロックのチェック
                if isinstance(raw, str):
                    try:
                        raw_obj = json.loads(raw)
                        if isinstance(raw_obj, dict) and raw_obj.get("error") == "guardrail_blocked":
                            print(
                                "[WARN] Guardrail blocked content generation",
                                {"requestId": aws_request_id, "refresh": refresh, "attempt": attempt},
                            )
                            # guardrailブロックの場合は次のrefreshへ
                            break
                    except json.JSONDecodeError:
                        pass  # JSON以外の文字列なので通常処理へ

                try:
                    cleaned, reason = _rescue_json_text(raw if isinstance(raw, str) else "")
                    if reason:
                        print(
                            "[INFO] rescued json text before parsing",
                            {
                                "requestId": aws_request_id,
                                "refresh": refresh,
                                "attempt": attempt,
                                "reason": reason,
                                "rawLen": len(raw) if isinstance(raw, str) else -1,
                                "cleanedLen": len(cleaned) if isinstance(cleaned, str) else -1,
                            },
                        )
                    obj = parse_json_strict(cleaned if reason else raw, LIMITS["raw_json_max"])
                    quiz = validate_generation(obj)
                except (ParseError, SchemaError, SemanticError) as e:
                    print(
                        f"[WARN] generation failed ({e.code}): {e.message}",
                        {"requestId": aws_request_id, "refresh": refresh, "attempt": attempt},
                    )
                    if isinstance(raw, str):
                        print(f"[WARN] raw(head): {raw[:300]}")
                    continue

                # --- sanitize inputs for hashing ---
                safe_title = _sanitize_for_hash(quiz["title"])
                safe_body = _sanitize_for_hash(quiz["body"])
                safe_must_points = []
                for p in quiz["rubric"]["mustHavePoints"]:
                    safe_must_points.append(
                        {
                            "id": _sanitize_for_hash(p.get("id", "")),
                            "label": _sanitize_for_hash(p.get("label", "")),
                            "notes": _sanitize_for_hash(p.get("notes", "")),
                            "keywords_any": [_sanitize_for_hash(x) for x in (p.get("keywords_any") or [])],
                        }
                    )

                qhash = question_hash(
                    category=quiz["category"],
                    level=int(quiz["level"]),
                    title=safe_title,
                    body=safe_body,
                    must_points=safe_must_points,
                )

                created_at = now_iso_jst()

                item = {
                    "QuestionHash": qhash,
                    "GSI1PK": "RECENT",
                    "GSI1SK": created_at,
                    "Version": 1,
                    "Category": quiz["category"],
                    "Level": int(quiz["level"]),
                    "Language": "ja",
                    "Question": {"Title": quiz["title"], "Body": quiz["body"]},
                    "Rubric": quiz["rubric"],
                    "SourceContext": {
                        "Provider": "aws-knowledge-mcp",
                        "RetrievedAt": created_at,
                        "Snippets": [
                            {"id": f"s{i}", "text": t, "source": "AWS (via MCP)", "url": None}
                            for i, t in enumerate(snippets[:SOURCE_SNIPPETS_MAX], start=1)
                        ],
                    },
                    "CreatedAt": created_at,
                    "Tags": quiz["tags"],
                }

                # DynamoDB safe (float -> Decimal)
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

                # advance cursor by exactly one "candidate step" when we succeed OR when we hit duplicates
                # (prevents next invocation from repeating the same query and generating the same hash again)
                _advance_cursor_next_idx(
                    QUIZ_TABLE_NAME,
                    category,
                    level,
                    expected_old=q_idx,
                    new_value=(q_idx + 1),
                )

                if ok:
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

                # IMPORTANT: do NOT retry within this request (prevents 2nd Bedrock call)
                # Let the caller retry; next invocation will use advanced cursor.
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
