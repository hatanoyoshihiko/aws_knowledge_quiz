"""
Rubric生成関数（非同期実行）
get_next_quizから呼び出され、バックグラウンドでrubricを生成してDDBに保存
"""
from __future__ import annotations

import os
import json
from decimal import Decimal
import boto3

from common.bedrock import BedrockClient
from common.config import (
    BEDROCK_MODEL_ID,
    BEDROCK_GUARDRAIL_IDENTIFIER,
    BEDROCK_GUARDRAIL_VERSION,
    QUIZ_TABLE_NAME,
)
from common.ddb import QuizRepo
from common.errors import ParseError, SchemaError, SemanticError
from common.schema import LIMITS
from common.validate import parse_json_strict, validate_rubric


def _to_ddb_safe(x):
    """Convert float to Decimal for DynamoDB compatibility"""
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


def lambda_handler(event, context):
    """
    event = {
        "questionHash": "...",
        "category": "security",
        "level": 100,
        "title": "...",
        "body": "...",
        "sourceContext": "...",
        "requestId": "..."
    }
    """
    try:
        question_hash = event.get("questionHash")
        category = event.get("category")
        level = event.get("level")
        title = event.get("title")
        body = event.get("body")
        source_context = event.get("sourceContext", "")
        request_id = event.get("requestId", "-")

        print(
            "[INFO] generate_rubric started",
            {
                "requestId": request_id,
                "questionHash": question_hash,
                "category": category,
                "level": level,
            },
        )

        if not question_hash or not title or not body:
            print("[ERROR] Missing required fields")
            return {"statusCode": 400, "body": "Missing required fields"}

        # Bedrock client
        brt = boto3.client("bedrock-runtime")
        bedrock = BedrockClient(
            brt,
            BEDROCK_MODEL_ID,
            guardrail_identifier=BEDROCK_GUARDRAIL_IDENTIFIER if BEDROCK_GUARDRAIL_IDENTIFIER else None,
            guardrail_version=BEDROCK_GUARDRAIL_VERSION if BEDROCK_GUARDRAIL_IDENTIFIER else None,
        )

        # Prompt ARN取得
        rubric_prompt_arn = os.environ.get("BEDROCK_RUBRIC_PROMPT_ARN", "").strip()
        if not rubric_prompt_arn:
            print("[ERROR] BEDROCK_RUBRIC_PROMPT_ARN not set")
            return {"statusCode": 500, "body": "BEDROCK_RUBRIC_PROMPT_ARN not set"}

        print(
            "[CFG] rubric prompt config",
            {
                "requestId": request_id,
                "BEDROCK_RUBRIC_PROMPT_ARN": _mask(rubric_prompt_arn, 18),
            },
        )

        # Prompt変数準備
        prompt_vars = {
            "category": str(category),
            "level": str(level),
            "title": str(title),
            "body": str(body),
            "source_context": str(source_context),
        }

        # Bedrock呼び出し
        print("[INFO] Calling Bedrock for rubric generation", {"requestId": request_id})
        
        raw = bedrock.converse_prompt_json(
            prompt_arn=rubric_prompt_arn,
            prompt_variables=prompt_vars,
        )

        raw_len = len(raw) if isinstance(raw, str) else -1
        print(
            "[INFO] Bedrock returned rubric",
            {"requestId": request_id, "rawLen": raw_len},
        )

        # Guardrailブロックチェック
        if isinstance(raw, str):
            try:
                raw_obj = json.loads(raw)
                if isinstance(raw_obj, dict) and raw_obj.get("error") == "guardrail_blocked":
                    print(
                        "[WARN] Guardrail blocked rubric generation",
                        {"requestId": request_id},
                    )
                    return {"statusCode": 200, "body": "Guardrail blocked"}
            except json.JSONDecodeError:
                pass

        # JSON解析
        try:
            obj = parse_json_strict(raw, LIMITS["raw_json_max"])
            rubric = validate_rubric(obj)
        except (ParseError, SchemaError, SemanticError) as e:
            print(
                f"[ERROR] rubric validation failed ({e.code}): {e.message}",
                {"requestId": request_id},
            )
            if isinstance(raw, str):
                print(f"[ERROR] raw(head): {raw[:300]}")
            return {"statusCode": 400, "body": f"Validation failed: {e.message}"}

        # DDB更新
        repo = QuizRepo(QUIZ_TABLE_NAME)
        
        # Convert float to Decimal for DynamoDB
        rubric_safe = _to_ddb_safe(rubric)
        
        success = repo.update_rubric(question_hash, rubric_safe)

        if success:
            print(
                "[INFO] Rubric saved successfully",
                {"requestId": request_id, "questionHash": question_hash},
            )
            return {"statusCode": 200, "body": "Rubric generated and saved"}
        else:
            print(
                "[WARN] Failed to update rubric (item may not exist)",
                {"requestId": request_id, "questionHash": question_hash},
            )
            return {"statusCode": 404, "body": "Question not found"}

    except Exception as e:
        print("[ERROR] Unexpected exception in generate_rubric")
        print("[ERROR] repr:", repr(e))
        import traceback
        traceback.print_exc()
        return {"statusCode": 500, "body": "Internal error"}
