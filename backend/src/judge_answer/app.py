from __future__ import annotations
import json

from decimal import Decimal
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

from common.config import BEDROCK_MODEL_ID, QUIZ_TABLE_NAME
from common.bedrock import BedrockClient
from common.ddb import QuizRepo
from common.validate import parse_json_strict, validate_judgment
from common.schema import LIMITS
from common.errors import AppError, ParseError, SchemaError, SemanticError

# NOTE:
# - mustPointsMet / missingMustPoints には mustHavePoints の id（例: "p1"）のみを入れる
# - niceToHavePoints の id（例: "n1"）は nicePointsMet に入れる
SYSTEM_PROMPT = """あなたはAWSクイズの採点者です。ルール:
- 採点根拠は RUBRIC と USER_ANSWER のみを使う。
- 出力は必ずJSONのみ（前後に文章を付けない）。
- result は correct / close / incorrect のいずれか。
- close は必須要点の約8割。RUBRIC.scoringPolicy に厳密に従う。
- feedback は最大2文で簡潔に。
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
        # 整数なら int、そうでなければ float
        return int(o) if o % 1 == 0 else float(o)
    raise TypeError(f"Not JSON serializable: {type(o)}")

def _extract_ids(rubric: dict, key: str) -> list[str]:
    """
    rubric[key] が [{"id":"p1", ...}, ...] の配列である前提で id を取り出す。
    """
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
    """
    rubric 内（mustHavePoints / niceToHavePoints / commonWrongClaims）から id一致の要素を返す。
    """
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
    """
    id（p1/n1/w1など）から UI 表示用の文言を返す。
    get_next_quiz 側の rubric では description ではなく label が本体。
    """
    try:
        it = _find_point_by_id(rubric, pid)
        if not it:
            return ""
        label = it.get("label")
        return label if isinstance(label, str) else ""
    except Exception:
        return ""

def _normalize_p(pid: str) -> str:
    # "p1" -> "P1", "n1" -> "N1"
    s = str(pid or "").strip()
    return s.upper()

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

        question_hash = question_id.strip()

        repo = QuizRepo(QUIZ_TABLE_NAME)
        item = repo.get_by_hash(question_hash)
        if not item:
            raise AppError("NotFound", "Question not found", 404)

        rubric = item["Rubric"]
        question_body = item["Question"]["Body"]

        # MUST/NICE の id 一覧を明示して、Bedrock に混入させない
        must_ids = _extract_ids(rubric, "mustHavePoints")
        nice_ids = _extract_ids(rubric, "niceToHavePoints")

        bedrock = BedrockClient()
        user_prompt = USER_PROMPT_TEMPLATE.format(
            question_body=question_body,
            rubric_json=json.dumps(rubric, ensure_ascii=False, default=_json_default),
            user_answer_text=answer_text.strip(),
            must_ids_json=json.dumps(must_ids, ensure_ascii=False),
            nice_ids_json=json.dumps(nice_ids, ensure_ascii=False),
        )

        raw = bedrock.converse_json(
            model_id=BEDROCK_MODEL_ID,
            system=SYSTEM_PROMPT,
            user=user_prompt,
        )

        obj = parse_json_strict(raw, LIMITS["raw_json_max_judge"])

        # validate_judgment が unknown field を許容しない場合に備えて、
        # nicePointsMet は validate 前に除去（must/nice 混入の修正はプロンプトで実施）
        if isinstance(obj, dict) and "nicePointsMet" in obj:
            try:
                obj = dict(obj)
                obj.pop("nicePointsMet", None)
            except Exception:
                pass

        judge = validate_judgment(obj, rubric)

        # UI表示用（P/N + label）を返す：既存レスポンスは維持しつつ details を追加
        must_points = judge.get("mustPointsMet") or []
        missing_points = judge.get("missingMustPoints") or []

        must_details = []
        for pid in must_points:
            if not isinstance(pid, str):
                continue
            must_details.append(
                {
                    "p": _normalize_p(pid),
                    "label": _id_to_label(pid, rubric),
                }
            )

        missing_details = []
        for pid in missing_points:
            if not isinstance(pid, str):
                continue
            missing_details.append(
                {
                    "p": _normalize_p(pid),
                    "label": _id_to_label(pid, rubric),
                }
            )

        return _resp(
            200,
            {
                "result": judge["result"],
                "score": float(judge["score"]),
                "mustPointsMet": judge["mustPointsMet"],
                "missingMustPoints": judge["missingMustPoints"],
                # 追加フィールド（UI向け）
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
