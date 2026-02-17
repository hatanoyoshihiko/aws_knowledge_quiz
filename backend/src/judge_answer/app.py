from __future__ import annotations

import json
import logging
from decimal import Decimal

import boto3

from common.bedrock import BedrockClient
from common.config import BEDROCK_MODEL_ID, BEDROCK_GUARDRAIL_IDENTIFIER, BEDROCK_GUARDRAIL_VERSION, QUIZ_TABLE_NAME, BEDROCK_MAX_TOKENS_JUDGE
from common.ddb import QuizRepo
from common.errors import AppError, ParseError, SchemaError, SemanticError
from common.schema import LIMITS
from common.validate import parse_json_strict, validate_judgment

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SYSTEM_PROMPT = """あなたはAWSクイズの採点者です。ルール:
- 採点根拠は RUBRIC と USER_ANSWER のみを使う。
- 出力は必ずJSONのみ（前後に文章を付けない）。
- result は correct / close / incorrect のいずれか。
- close は必須要点の約8割。RUBRIC.scoringPolicy に厳密に従う。
- feedback は【絶対に400文字以内】で簡潔に。超過厳禁。
- nextHint は【絶対に280文字以内】で簡潔に。超過厳禁。
- 同義表現は加点してよい。不要に厳密な言い回しにはしない。
"""

USER_PROMPT_TEMPLATE = """次のクイズを採点してください。

[QUESTION]
{question_body}

[RUBRIC]
{rubric_json}

[POINT_IDS]
- MUST_HAVE_POINT_IDS: {must_ids_json}
- NICE_TO_HAVE_POINT_IDS: {nice_ids_json}

[USER_ANSWER]
{user_answer_text}

[OUTPUT_JSON_SCHEMA]
{{
  "result": "correct|close|incorrect",
  "score": 0.0,
  "mustPointsMet": ["p1","p2"],
  "missingMustPoints": ["p3"],
  "nicePointsMet": ["n1"],
  "feedback": "string",
  "nextHint": "string"
}}

採点方法:
- mustHavePoints の各要点について、USER_ANSWERが満たすかを判断する。
- 満たした must point 数を数え、RUBRIC.scoringPolicy に従って result を決める。
- score は must点の充足率（0〜1）を基本とし、niceToHaveで最大+0.1まで上乗せしてよい（ただし1.0を超えない）。
- 回答コメントはトンチを利かしたり、ユーモアに富んだ文章とする。

文字数制約（厳守）:
- feedback: 【絶対に400文字以内】。超過した場合はエラーになります。
- nextHint: 【絶対に280文字以内】。超過した場合はエラーになります。
- 簡潔で要点を押さえたコメントにしてください。

制約（重要）:
- mustPointsMet と missingMustPoints は MUST_HAVE_POINT_IDS のみを使用する（NICE の id を入れない）。
- nicePointsMet は NICE_TO_HAVE_POINT_IDS のみを使用する（MUST の id を入れない）。
- 配列内の要素は重複させない。
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


def _extract_ids(rubric: dict, key: str) -> list[str]:
    out: list[str] = []
    if not isinstance(rubric, dict):
        return out
    arr = rubric.get(key)
    if not isinstance(arr, list):
        return out
    for it in arr:
        if not isinstance(it, dict):
            continue
        pid = it.get("id")
        if isinstance(pid, str) and pid.strip():
            out.append(pid.strip())
    return out


def _find_point_by_id(rubric: dict, pid: str) -> dict | None:
    if not isinstance(rubric, dict) or not isinstance(pid, str):
        return None
    key = pid.strip().lower()
    for k in ("mustHavePoints", "niceToHavePoints", "commonWrongClaims"):
        arr = rubric.get(k)
        if not isinstance(arr, list):
            continue
        for it in arr:
            if not isinstance(it, dict):
                continue
            it_id = it.get("id")
            if isinstance(it_id, str) and it_id.strip().lower() == key:
                return it
    return None


def _id_to_label(pid: str, rubric: dict) -> str:
    try:
        it = _find_point_by_id(rubric, pid)
        if not it:
            return ""
        label = it.get("label")
        return label if isinstance(label, str) else ""
    except Exception:
        return ""


def _normalize_p(pid: str) -> str:
    return str(pid or "").strip().upper()


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


def _call_bedrock_judge_json(bedrock: BedrockClient, *, system: str, user: str) -> object:
    system_param = _to_system_param(system)
    messages = [{"role": "user", "content": [{"text": user}]}]

    if not hasattr(bedrock, "converse_json"):
        raise AttributeError("BedrockClient has no converse_json")

    # Layer 実装（messages+system(list)）に合わせる
    try:
        inference_config = {
            "maxTokens": BEDROCK_MAX_TOKENS_JUDGE,
            "temperature": 0.05
        }
        if system_param is None:
            return bedrock.converse_json(messages=messages, inferenceConfig=inference_config)
        return bedrock.converse_json(messages=messages, system=system_param, inferenceConfig=inference_config)
    except TypeError:
        # 旧実装へのフォールバック
        try:
            return bedrock.converse_json(model_id=BEDROCK_MODEL_ID, system=system, user=user)
        except TypeError:
            return bedrock.converse_json(system=system, user=user)


def _coerce_bedrock_output_to_obj(raw: object) -> dict:
    """
    BedrockClient.converse_json の返り値揺れを吸収して dict にする。
    - dict ならそのまま
    - str なら JSON として parse
    - dict に {"json": "..."} / {"text": "..."} のように包まれているケースも救う
    """
    if isinstance(raw, dict):
        # guardrailブロックの場合
        if raw.get("error") == "guardrail_blocked":
            raise AppError(
                "GUARDRAIL_BLOCKED",
                "回答内容がガードレールポリシーによってブロックされました。適切な内容で再度お試しください。",
                400,
            )
        
        # すでに採点結果が入っているならそのまま返す
        if "result" in raw and "score" in raw:
            return raw

        # ラップ形式っぽいキーがある場合に救う
        for k in ("json", "text", "content"):
            v = raw.get(k)
            if isinstance(v, str):
                return parse_json_strict(v, LIMITS["raw_json_max_judge"])

        # Bedrockの生レスポンスをそのまま返してしまってる場合はここで落とす
        raise AppError(
            "PARSE_ERROR",
            "Bedrock response dict does not look like judgment JSON",
            400,
        )

    if isinstance(raw, str):
        return parse_json_strict(raw, LIMITS["raw_json_max_judge"])

    raise AppError(
        "PARSE_ERROR",
        f"Bedrock response must be string or dict, got {type(raw)}",
        400,
    )


def lambda_handler(event, context):
    try:
        raw_body = event.get("body") or ""
        try:
            req = json.loads(raw_body)
        except Exception:
            raise AppError("BadRequest", "Request body must be JSON", 400)

        question_id = req.get("questionId")
        answer_text = req.get("answerText")

        if not isinstance(question_id, str) or not question_id.strip():
            raise AppError("BadRequest", "questionId is required", 400)
        if not isinstance(answer_text, str) or not answer_text.strip():
            raise AppError("BadRequest", "answerText is required", 400)
        
        # Validate answer length
        answer_len = len(answer_text.strip())
        if answer_len < LIMITS["answer_text_min"]:
            raise AppError("BadRequest", f"回答は{LIMITS['answer_text_min']}文字以上で入力してください", 400)
        if answer_len > LIMITS["answer_text_max"]:
            raise AppError("BadRequest", f"回答は{LIMITS['answer_text_max']}文字以内で入力してください", 400)

        question_hash = question_id.strip()

        repo = QuizRepo(QUIZ_TABLE_NAME)
        item = repo.get_by_hash(question_hash)
        if not item:
            raise AppError("NotFound", "Question not found", 404)

        rubric = item["Rubric"]
        question_body = item["Question"]["Body"]

        must_ids = _extract_ids(rubric, "mustHavePoints")
        nice_ids = _extract_ids(rubric, "niceToHavePoints")

        brt = boto3.client("bedrock-runtime")
        bedrock = BedrockClient(
            brt,
            BEDROCK_MODEL_ID,
            guardrail_identifier=BEDROCK_GUARDRAIL_IDENTIFIER if BEDROCK_GUARDRAIL_IDENTIFIER else None,
            guardrail_version=BEDROCK_GUARDRAIL_VERSION if BEDROCK_GUARDRAIL_IDENTIFIER else None,
        )

        user_prompt = USER_PROMPT_TEMPLATE.format(
            question_body=question_body,
            rubric_json=json.dumps(rubric, ensure_ascii=False, default=_json_default),
            user_answer_text=answer_text.strip(),
            must_ids_json=json.dumps(must_ids, ensure_ascii=False),
            nice_ids_json=json.dumps(nice_ids, ensure_ascii=False),
        )

        raw = _call_bedrock_judge_json(bedrock, system=SYSTEM_PROMPT, user=user_prompt)

        # ★ここが重要：raw の型揺れを吸収して dict にする
        obj = _coerce_bedrock_output_to_obj(raw)

        # validate_judgment が unknown field を許容しない場合に備えて nicePointsMet を除去
        if "nicePointsMet" in obj:
            obj = dict(obj)
            obj.pop("nicePointsMet", None)

        judge = validate_judgment(obj, rubric)

        must_points = judge.get("mustPointsMet") or []
        missing_points = judge.get("missingMustPoints") or []

        must_details = []
        for pid in must_points:
            if not isinstance(pid, str):
                continue
            must_details.append({"p": _normalize_p(pid), "label": _id_to_label(pid, rubric)})

        missing_details = []
        for pid in missing_points:
            if not isinstance(pid, str):
                continue
            missing_details.append({"p": _normalize_p(pid), "label": _id_to_label(pid, rubric)})

        return _resp(
            200,
            {
                "result": judge["result"],
                "score": float(judge["score"]),
                "mustPointsMet": judge["mustPointsMet"],
                "missingMustPoints": judge["missingMustPoints"],
                "mustPointsMetDetails": must_details,
                "missingMustPointsDetails": missing_details,
                "feedback": judge["feedback"],
                "nextHint": judge.get("nextHint", ""),
            },
        )

    except AppError as e:
        return _resp(e.status_code, {"error": {"code": e.code, "message": e.message}})
    except (ParseError, SchemaError, SemanticError) as e:
        status = getattr(e, "status_code", 400)
        code = getattr(e, "code", e.__class__.__name__)
        msg = getattr(e, "message", str(e))
        return _resp(status, {"error": {"code": code, "message": msg}})
    except Exception:
        logger.exception("Unexpected error in judge_answer")
        return _resp(500, {"error": {"code": "InternalError", "message": "Unexpected error"}})
