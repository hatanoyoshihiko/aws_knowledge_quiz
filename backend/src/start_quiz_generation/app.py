"""
非同期クイズ生成開始エンドポイント
即座に202を返し、バックグラウンドでクイズ生成
"""
from __future__ import annotations

import json
import boto3
from urllib.parse import parse_qs

from common.config import HOST_KEY
from common.errors import AppError
from common.schema import ALLOWED_CATEGORIES, ALLOWED_LEVELS

_lambda_client = boto3.client('lambda')


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


def lambda_handler(event, context):
    try:
        method = (event.get("requestContext", {}).get("http", {}).get("method") or "").upper()
        if method == "OPTIONS":
            return _resp(200, {"ok": True}, event)

        # Host key check
        expected = (HOST_KEY or "").strip()
        if expected and not expected.startswith("CHANGE_ME"):
            headers = event.get("headers") or {}
            got = headers.get("x-host-key") or headers.get("X-Host-Key")
            if not got or str(got).strip() != expected:
                raise AppError("Forbidden", "Host key is required", 403)

        # Parse parameters
        qs = parse_qs((event.get("rawQueryString") or ""))
        category = (qs.get("category", [None])[0] or "security").strip()
        level_str = (qs.get("level", [None])[0] or "100").strip()

        if category not in ALLOWED_CATEGORIES:
            raise AppError("BadRequest", f"Invalid category: {category}", 400)

        try:
            level = int(level_str)
        except ValueError:
            raise AppError("BadRequest", f"Invalid level: {level_str}", 400)

        if level not in ALLOWED_LEVELS:
            raise AppError("BadRequest", f"Invalid level: {level}", 400)

        # Invoke get_next_quiz asynchronously
        import os
        target_function = os.environ.get('GET_NEXT_QUIZ_FUNCTION_NAME')
        if not target_function:
            raise AppError("ConfigError", "GET_NEXT_QUIZ_FUNCTION_NAME not set", 500)

        payload = {
            "rawQueryString": f"category={category}&level={level}",
            "headers": event.get("headers", {}),
            "requestContext": event.get("requestContext", {})
        }

        _lambda_client.invoke(
            FunctionName=target_function,
            InvocationType='Event',  # Async
            Payload=json.dumps(payload).encode('utf-8')
        )

        print(f"[INFO] Started async quiz generation: category={category}, level={level}")

        # Return 202 Accepted immediately
        return _resp(
            202,
            {
                "status": "generating",
                "message": "クイズ生成を開始しました。数秒後に最新のクイズが取得できます。",
                "category": category,
                "level": level
            },
            event
        )

    except AppError as e:
        return _resp(e.status_code, {"error": {"code": e.code, "message": e.message}}, event)
    except Exception as e:
        print("[ERROR] Unexpected exception in start_quiz_generation")
        print("[ERROR] repr:", repr(e))
        import traceback
        traceback.print_exc()
        return _resp(500, {"error": {"code": "InternalError", "message": "Unexpected error"}}, event)
