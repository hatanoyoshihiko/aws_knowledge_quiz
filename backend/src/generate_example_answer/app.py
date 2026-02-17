from __future__ import annotations

import json
import logging
from decimal import Decimal

import boto3

from common.bedrock import BedrockClient
from common.config import BEDROCK_MODEL_ID, BEDROCK_GUARDRAIL_IDENTIFIER, BEDROCK_GUARDRAIL_VERSION, QUIZ_TABLE_NAME, BEDROCK_MAX_TOKENS_EXAMPLE
from common.ddb import QuizRepo
from common.errors import AppError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SYSTEM_PROMPT = """あなたはAWS教育用の模範解答作成者です。ルール:
- 問題文とRubricを参照し、100点満点の回答例を生成する
- すべてのmustHavePointsとniceToHavePointsを満たす
- 簡潔で分かりやすい日本語で記述
- 【絶対に400文字以内】で完結させる。超過厳禁。
- 技術的に正確で、実務で使える内容にする
- 出力は純粋なテキストのみ（JSONやマークダウン不要）
"""

USER_PROMPT_TEMPLATE = """次の問題に対する100点満点の模範解答を生成してください。

[QUESTION]
{question_body}

[RUBRIC]
{rubric_json}

[MUST_HAVE_POINTS]
{must_points_json}

[NICE_TO_HAVE_POINTS]
{nice_points_json}

制約:
- 【絶対に400文字以内】で完結させる
- すべてのmustHavePointsを必ず含める
- 可能な限りniceToHavePointsも含める
- 簡潔で分かりやすい表現を使う
- 技術的に正確な内容にする

出力形式:
- 純粋なテキストのみ
- JSONやマークダウン記法は不要
- 説明文や前置きは不要
"""


def _resp(status: int, body: dict):
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json; charset=utf-8"},
        "body": json.dumps(body, ensure_ascii=False),
    }


def _json_default(o):
    if isinstance(o, Decimal):
        return int(o) if o % 1 == 0 else float(o)
    raise TypeError(f"Not JSON serializable: {type(o)}")


def _extract_points(rubric: dict, key: str) -> list[dict]:
    """Extract points with full details from rubric"""
    out: list[dict] = []
    if not isinstance(rubric, dict):
        return out
    arr = rubric.get(key)
    if not isinstance(arr, list):
        return out
    for it in arr:
        if not isinstance(it, dict):
            continue
        out.append(it)
    return out


def _to_system_param(system: str | list | tuple | None):
    if system is None:
        return None
    if isinstance(system, (list, tuple)):
        return list(system)
    if isinstance(system, str):
        s = system.strip()
        if not s:
            return None
        return [{"text": s}]
    raise TypeError(f"Invalid system type: {type(system)}")


def _call_bedrock_generate_text(bedrock: BedrockClient, *, system: str, user: str) -> str:
    """Call Bedrock to generate plain text response"""
    system_param = _to_system_param(system)
    messages = [{"role": "user", "content": [{"text": user}]}]

    if not hasattr(bedrock, "converse"):
        raise AttributeError("BedrockClient has no converse")

    try:
        inference_config = {
            "maxTokens": BEDROCK_MAX_TOKENS_EXAMPLE,
            "temperature": 0.15
        }
        if system_param is None:
            raw = bedrock.converse(messages=messages, inferenceConfig=inference_config)
        else:
            raw = bedrock.converse(messages=messages, system=system_param, inferenceConfig=inference_config)
    except TypeError:
        raise AppError("BEDROCK_ERROR", "Bedrock API call failed", 500)

    # Extract text from response
    stop_reason = raw.get("stopReason")
    if stop_reason == "guardrail_intervened":
        raise AppError(
            "GUARDRAIL_BLOCKED",
            "生成内容がガードレールポリシーによってブロックされました。",
            400,
        )

    parts = raw.get("output", {}).get("message", {}).get("content", [])
    text_parts = []
    if isinstance(parts, list):
        for p in parts:
            if isinstance(p, dict) and "text" in p and isinstance(p["text"], str):
                text_parts.append(p["text"])

    return "".join(text_parts).strip()


def lambda_handler(event, context):
    try:
        # Parse query parameters
        params = event.get("queryStringParameters") or {}
        question_id = params.get("questionId")

        if not isinstance(question_id, str) or not question_id.strip():
            raise AppError("BadRequest", "questionId is required", 400)

        question_hash = question_id.strip()

        # Get question from DynamoDB
        repo = QuizRepo(QUIZ_TABLE_NAME)
        item = repo.get_by_hash(question_hash)
        if not item:
            raise AppError("NotFound", "Question not found", 404)

        rubric = item["Rubric"]
        question_body = item["Question"]["Body"]

        # Extract points with full details
        must_points = _extract_points(rubric, "mustHavePoints")
        nice_points = _extract_points(rubric, "niceToHavePoints")

        # Initialize Bedrock client
        brt = boto3.client("bedrock-runtime")
        bedrock = BedrockClient(
            brt,
            BEDROCK_MODEL_ID,
            guardrail_identifier=BEDROCK_GUARDRAIL_IDENTIFIER if BEDROCK_GUARDRAIL_IDENTIFIER else None,
            guardrail_version=BEDROCK_GUARDRAIL_VERSION if BEDROCK_GUARDRAIL_IDENTIFIER else None,
        )

        # Build prompt
        user_prompt = USER_PROMPT_TEMPLATE.format(
            question_body=question_body,
            rubric_json=json.dumps(rubric, ensure_ascii=False, default=_json_default),
            must_points_json=json.dumps(must_points, ensure_ascii=False, default=_json_default),
            nice_points_json=json.dumps(nice_points, ensure_ascii=False, default=_json_default),
        )

        # Generate example answer
        example_answer = _call_bedrock_generate_text(bedrock, system=SYSTEM_PROMPT, user=user_prompt)

        # Validate length
        if len(example_answer) > 450:
            logger.warning(f"Generated answer is too long: {len(example_answer)} chars")
            example_answer = example_answer[:400] + "..."

        return _resp(
            200,
            {
                "exampleAnswer": example_answer,
                "questionId": question_hash,
            },
        )

    except AppError as e:
        return _resp(e.status_code, {"error": {"code": e.code, "message": e.message}})
    except Exception:
        logger.exception("Unexpected error in generate_example_answer")
        return _resp(500, {"error": {"code": "InternalError", "message": "Unexpected error"}})
