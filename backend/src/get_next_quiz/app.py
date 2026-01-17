from __future__ import annotations

import json
from decimal import Decimal

import boto3
from urllib.parse import parse_qs

from common.bedrock import BedrockClient
from common.config import (
    BEDROCK_MODEL_ID,
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
# Bedrock prompts
# -----------------------------

SYSTEM_PROMPT = """あなたはAWSソリューションアーキテクト兼、社内教育用のAWSクイズ作成者です。
ルール:
- 入力で与えられる SOURCE_CONTEXT のみを根拠にすること。
- SOURCE_CONTEXTにない事実を断定しない。不明なら出題しない。
- 日本語で、社内学習に適した明確な問題にする。
- 出力は必ずJSONのみ（前後に文章やコードフェンスを付けない）。
- 1問だけ生成する。
- rubric（Must/Nice/Wrong/ScoringPolicy）を必ず含める。
- level は AWSセッションレベル（100/200/300/400）を数値で使用する。
  - 100: 基礎概念、200: 設計/推奨、300: 実装/運用、400: 専門家/トレードオフ
- ScoringPolicyは correct=100%, close=80% を満たすように定義する。
- クイズはトンチを利かしたり、ユーモアに富んだ文章とする。
- 誤解を招く表現はしないこと。
- 設問に対しての回答を明確に求め、YESやNOの表現だけを回答として求めないこと。
- 現在時刻で廃止が決定されている仕組（SSE-C等）は出題しないこと。
"""

USER_PROMPT_TEMPLATE = """次の制約でAWSクイズを1問生成してください。

[CONSTRAINTS]
- 文字数制約（必ず守る）:
  - title: 60文字以内
  - body: 400文字以内
  - sourceSummary: 300文字以内
  - rubric.expectedAnswer: 400文字以内
  - mustHavePoints.label: 60文字以内、notes: 120文字以内
  - keywords_any: 各要素 20文字以内、各ポイント最大 8 個
- JSONはコードフェンス禁止（```json など禁止）
- JSON以外の文章を一切出力しない
- category: {category}
- level: {level}
- language: ja
- avoid_duplicate_hint:
{avoid_duplicate_hint}

[SOURCE_CONTEXT]
{source_context}

[OUTPUT_JSON_SCHEMA]
{{
  "title": "string",
  "body": "string",
  "category": "string",
  "level": 0,
  "rubric": {{
    "expectedAnswer": "string",
    "mustHavePoints": [
      {{"id":"p1","label":"string","keywords_any":["string"],"notes":"string"}}
    ],
    "niceToHavePoints": [
      {{"id":"n1","label":"string","keywords_any":["string"],"notes":"string"}}
    ],
    "commonWrongClaims": [
      {{"id":"w1","label":"string","keywords_any":["string"],"notes":"string"}}
    ],
    "scoringPolicy": {{
      "correct_threshold": 1.0,
      "close_threshold": 0.8,
      "must_points_total": 0,
      "close_if_must_points_met_at_least": 0,
      "correct_if_must_points_met_at_least": 0
    }}
  }},
  "sourceSummary": "string",
  "tags": ["string"]
}}

mustHavePointsは4〜6個にしてください。
"""

# -----------------------------
# MCP query sets
# -----------------------------

QUERYSETS: dict[str, list[str]] = {
    "security": [
        "IAM ポリシー 評価ロジック 明示的Deny Allow 優先順位",
        "IAM 最小権限 ベストプラクティス ロール 利用 推奨",
        "S3 公開 防止 Block Public Access バケットポリシー 注意点",
        "クロスアカウント AssumeRole 信頼ポリシー ExternalId 設計",
        "S3 サーバー側暗号化 SSE-S3 SSE-KMS SSE-C 違い 監査",
    ],
    "networking": [
        "VPC ルートテーブル IGW NAT Gateway サブネット 基礎",
        "VPC エンドポイント Gateway Interface 違い 使い分け",
        "セキュリティグループ NACL 違い ステートフル ステートレス",
        "ハイブリッド接続 Site-to-Site VPN Transit Gateway Direct Connect 選定 観点",
        "ALB NLB GLB VPC Route Server API Gateway 使い分け",
    ],
    "storage": [
        "S3 ストレージクラス ライフサイクル 移行 仕組み",
        "S3 バージョニング オブジェクトロック 削除保護 推奨",
        "S3 リクエスト料金 小さいオブジェクト 大量 注意点",
        "EBS スナップショット AMI 仕組み 復元 手順 概要",
        "EFS FSx EBS 使い分け",
        "DataSync Storage Gateway",
    ],
    "serverless": [
        "Lambda 同時実行 スロットリング リトライ 挙動",
        "Lambda 非同期 同期 DLQ On-failure 宛先 推奨",
        "Lambda タイムアウト 設計 VPC 接続 注意点",
        "Step Functions オーケストレーション パターン 例",
        "EventBridge SQS SNS 使い分け",
        "DynamoDB LSI GSI 違い",
        "Fargate",
        "Amazon SNS",
        "Amazon SQS",
        "AppSync",
        "Amplify",
    ],
    "well-architected": [
        "Well-Architected フレームワーク 6本柱 要点",
        "運用上の優秀性 監視 変更管理 インシデント対応 ベストプラクティス",
        "信頼性 単一障害点 フェイルオーバー 誤解 典型",
        "コスト最適化 タグ設計 可視化 実践",
        "責任共有モデル セキュリティ コンプライアンス 違い",
    ],
}


def _level_suffix(level: int) -> str:
    return {
        100: "入門 基礎 概要",
        200: "ベストプラクティス 設計 推奨 機能の詳細",
        300: "設定 運用 実装 トラブルシュート 注意点",
        400: "設計トレードオフ 複数サービスやアーキテクチャによる実装 アンチパターン 深掘り",
    }[level]



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
    s += lines("RECENT_TITLES", h.get("recentTitles", []), 20)
    s += "\n" + lines("RECENT_TAGS", h.get("recentTags", []), 30)
    s += "\n" + lines("RECENT_MUST_POINTS", h.get("recentMustLabels", []), 30)
    s += "\nNOTE:\n- 直近と同じ論点・同じ言い回しを避け、別の観点で作問してください。\n"
    return s


def _build_source_context(snippets: list[str]) -> str:
    lines = ["SOURCE_SNIPPETS (max 8):"]
    for i, t in enumerate(snippets[:SOURCE_SNIPPETS_MAX], start=1):
        t2 = t.strip()
        if len(t2) > 600:
            t2 = t2[:600] + "…"
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
        bedrock = BedrockClient()
        mcp = McpClient(MCP_ENDPOINT, MCP_API_KEY)

        # ---- duplicate-avoid hints from DDB (GSI_Recent) ----
        hints = repo.get_recent_hints(DUPLICATE_HINT_WINDOW)
        avoid_hint = _make_avoid_hint(hints)

        # ---- MCP refresh loop (query rotation) ----
        base_queries = QUERYSETS[category]
        start_idx = _get_cursor_next_idx(QUIZ_TABLE_NAME, category, level)
        max_refresh = min(MAX_MCP_REFRESH, max(0, len(base_queries) - 1))

        refresh = 0
        while refresh <= max_refresh:
            q_idx = (start_idx + refresh) % len(base_queries)
            query = base_queries[q_idx] + _level_suffix(level)

            print("[INFO] MCP search", {"requestId": aws_request_id, "refresh": refresh, "query": query})

            mcp_snippets = mcp.search(query=query, max_snippets=SOURCE_SNIPPETS_MAX)
            snippets = [s.text for s in mcp_snippets]

            source_context = _build_source_context(snippets)

            # ---- generation attempts loop ----
            broke_for_time = False
            for attempt in range(1, MAX_ATTEMPTS + 1):
                remaining_ms = context.get_remaining_time_in_millis()
                if remaining_ms < 6000:
                    print(
                        "[WARN] Not enough time remaining; stop generation loop",
                        {"requestId": aws_request_id, "remainingMs": remaining_ms, "refresh": refresh, "attempt": attempt},
                    )
                    broke_for_time = True
                    break

                user_prompt = USER_PROMPT_TEMPLATE.format(
                    category=category,
                    level=level,
                    avoid_duplicate_hint=avoid_hint,
                    source_context=source_context,
                )

                raw = bedrock.converse_json(
                    model_id=BEDROCK_MODEL_ID,
                    system=SYSTEM_PROMPT,
                    user=user_prompt,
                )

                raw_len = len(raw) if isinstance(raw, str) else -1
                print("[INFO] Bedrock returned", {"requestId": aws_request_id, "refresh": refresh, "attempt": attempt, "rawLen": raw_len})

                try:
                    obj = parse_json_strict(raw, LIMITS["raw_json_max"])
                    quiz = validate_generation(obj)
                except (ParseError, SchemaError, SemanticError) as e:
                    print(f"[WARN] generation failed ({e.code}): {e.message}", {"requestId": aws_request_id, "refresh": refresh, "attempt": attempt})
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

                if repo.put_unique(item):
                    _advance_cursor_next_idx(QUIZ_TABLE_NAME, category, level, expected_old=start_idx, new_value=(start_idx + refresh + 1))
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
