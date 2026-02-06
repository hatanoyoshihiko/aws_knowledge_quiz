from __future__ import annotations

import json
import logging
from decimal import Decimal

from boto3.dynamodb.conditions import Key

from common.config import QUIZ_TABLE_NAME
from common.ddb import QuizRepo
from common.errors import AppError

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _json_default(o):
    # boto3 DynamoDB may return numbers as Decimal
    if isinstance(o, Decimal):
        try:
            if o % 1 == 0:
                return int(o)
        except Exception:
            pass
        return float(o)
    raise TypeError(f"Object of type {o.__class__.__name__} is not JSON serializable")


def _resp(status: int, body: dict):
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json; charset=utf-8"},
        "body": json.dumps(body, ensure_ascii=False, default=_json_default),
    }


def _public_question(item: dict) -> dict:
    q = item.get("Question") or {}
    return {
        "questionId": item.get("QuestionHash", ""),
        "title": q.get("Title", ""),
        "body": q.get("Body", ""),
        "category": item.get("Category", ""),
        "level": int(item.get("Level", 0)) if isinstance(item.get("Level", 0), Decimal) else item.get("Level", 0),
        "createdAt": item.get("CreatedAt", ""),
    }


def lambda_handler(event, context):
    try:
        # Parse query parameters for filtering
        params = event.get("queryStringParameters") or {}
        min_created_at = params.get("minCreatedAt")  # ISO format timestamp

        repo = QuizRepo(QUIZ_TABLE_NAME)

        # newest first from RECENT GSI
        try:
            query_kwargs = {
                "IndexName": "GSI_Recent",
                "KeyConditionExpression": Key("GSI1PK").eq("RECENT"),
                "ScanIndexForward": False,
                "Limit": 10,  # Get more items to filter by timestamp
            }
            
            resp = repo.table.query(**query_kwargs)
        except Exception:
            logger.exception("Failed to query recent quiz")
            raise AppError("InternalError", "Failed to load current quiz", 500)

        items = resp.get("Items") or []
        
        # Filter by minCreatedAt if specified
        if min_created_at:
            items = [item for item in items if item.get("CreatedAt", "") >= min_created_at]
        
        if not items:
            return _resp(200, {"status": "empty", "question": None, "message": "まだクイズが出題されていません。出題者が『次のクイズ』を押してください。"})

        item = items[0]
        q = _public_question(item)
        if not q.get("questionId") or not q.get("title") or not q.get("body"):
            raise AppError("InternalError", "Stored quiz is invalid", 500)

        return _resp(200, {"question": q})

    except AppError as e:
        return _resp(e.status_code, {"error": {"code": e.code, "message": e.message}})
    except Exception:
        logger.exception("Unexpected error in get_current_quiz")
        return _resp(500, {"error": {"code": "InternalError", "message": "Unexpected error"}})
