from __future__ import annotations

import json
import logging
from decimal import Decimal

from common.config import QUIZ_TABLE_NAME
from common.ddb import QuizRepo
from common.errors import AppError

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _json_default(o):
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
    lvl = item.get("Level", 0)
    if isinstance(lvl, Decimal):
        lvl = int(lvl)
    return {
        "questionId": item.get("QuestionHash", ""),
        "title": q.get("Title", ""),
        "body": q.get("Body", ""),
        "category": item.get("Category", ""),
        "level": lvl,
        "createdAt": item.get("CreatedAt", ""),
    }


def lambda_handler(event, context):
    try:
        qs = (event.get("queryStringParameters") or {}) if isinstance(event, dict) else {}
        qid = (qs.get("questionId") or "").strip()
        if not qid:
            raise AppError("BadRequest", "questionId is required", 400)

        repo = QuizRepo(QUIZ_TABLE_NAME)
        try:
            resp = repo.table.get_item(Key={"QuestionHash": qid})
        except Exception:
            logger.exception("Failed to get quiz by id")
            raise AppError("InternalError", "Failed to load quiz", 500)

        item = resp.get("Item")
        if not item:
            return _resp(404, {"error": {"code": "NotFound", "message": "quiz not found"}})

        q = _public_question(item)
        if not q.get("questionId") or not q.get("title") or not q.get("body"):
            raise AppError("InternalError", "Stored quiz is invalid", 500)

        return _resp(200, {"question": q})

    except AppError as e:
        return _resp(e.status_code, {"error": {"code": e.code, "message": e.message}})
    except Exception:
        logger.exception("Unexpected error in get_quiz_by_id")
        return _resp(500, {"error": {"code": "InternalError", "message": "Unexpected error"}})
