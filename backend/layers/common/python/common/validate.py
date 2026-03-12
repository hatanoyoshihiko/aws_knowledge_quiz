from __future__ import annotations
from decimal import Decimal

import json
import re
from typing import Any, Dict, List

from .errors import ParseError, SchemaError, SemanticError
from .schema import LIMITS, ALLOWED_CATEGORIES, ALLOWED_LEVELS


# -----------------------------
# type helpers
# -----------------------------

def _is_str(x: Any) -> bool:
    return isinstance(x, str)


def _is_dict(x: Any) -> bool:
    return isinstance(x, dict)


def _is_list(x: Any) -> bool:
    return isinstance(x, list)


def _is_int(x: Any) -> bool:
    # bool is subclass of int in Python; exclude it
    if isinstance(x, bool):
        return False
    if isinstance(x, int):
        return True
    # DynamoDB numbers may come as Decimal
    if isinstance(x, Decimal):
        try:
            return x % 1 == 0
        except Exception:
            return False
    return False


def _strip(s: str) -> str:
    return s.strip()


# -----------------------------
# control-char sanitizer
# -----------------------------
# \u0000-\u001F (C0 controls) and \u007F-\u009F (DEL + C1 controls)
_CTRL_RE = re.compile(r"[\u0000-\u001F\u007F-\u009F]")


def _sanitize_text(s: str) -> str:
    """
    Remove disallowed control chars.
    Keep common whitespace: \\n, \\r, \\t.
    """
    s = s.replace("\n", "__NL__").replace("\r", "__CR__").replace("\t", "__TB__")
    s = _CTRL_RE.sub("", s)
    s = s.replace("__NL__", "\n").replace("__CR__", "\r").replace("__TB__", "\t")
    return s


# -----------------------------
# JSON parsing (robust)
# -----------------------------

def _strip_code_fence(s: str) -> str:
    s = s.strip()
    if not s.startswith("```"):
        return s
    lines = s.splitlines()
    if not lines:
        return s
    lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _extract_json_object(s: str) -> str | None:
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return s[start:end + 1]


def parse_json_strict(raw: str, raw_limit: int) -> Dict[str, Any]:
    if not _is_str(raw):
        raise ParseError("Bedrock response must be string")
    if len(raw.encode("utf-8")) > raw_limit:
        raise SchemaError("Bedrock JSON too large")

    s0 = raw.strip()
    s1 = _strip_code_fence(s0)

    for candidate in (s0, s1):
        try:
            obj = json.loads(candidate)
            if not _is_dict(obj):
                raise SchemaError("Top-level JSON must be an object")
            return obj
        except SchemaError:
            raise
        except Exception:
            pass

    extracted = _extract_json_object(s1) or _extract_json_object(s0)
    if extracted:
        try:
            obj = json.loads(extracted)
            if not _is_dict(obj):
                raise SchemaError("Top-level JSON must be an object")
            return obj
        except SchemaError:
            raise
        except Exception:
            pass

    raise ParseError("Invalid JSON from Bedrock")


# -----------------------------
# validation helpers
# -----------------------------

def _require_key(obj: Dict[str, Any], key: str) -> Any:
    if key not in obj:
        raise SchemaError(f"Missing required key: {key}")
    return obj[key]


def _require_str(
    obj: Dict[str, Any],
    key: str,
    *,
    min_len: int = 1,
    max_len: int | None = None,
    truncate: bool = False,
) -> str:
    v = _require_key(obj, key)
    if not _is_str(v):
        raise SchemaError(f"{key} must be string")
    v2 = _sanitize_text(_strip(v))
    if len(v2) < min_len:
        raise SchemaError(f"{key} is too short")
    if max_len is not None and len(v2) > max_len:
        if truncate:
            # 切り詰めて続行（UIを落とさない）
            v2 = v2[:max_len]
        else:
            raise SchemaError(f"{key} is too long (max {max_len})")
    return v2


def _require_int(obj: Dict[str, Any], key: str) -> int:
    v = _require_key(obj, key)
    if not _is_int(v):
        raise SchemaError(f"{key} must be integer")
    return v


def _require_list(obj: Dict[str, Any], key: str) -> List[Any]:
    v = _require_key(obj, key)
    if not _is_list(v):
        raise SchemaError(f"{key} must be list")
    return v


def _require_dict(obj: Dict[str, Any], key: str) -> Dict[str, Any]:
    v = _require_key(obj, key)
    if not _is_dict(v):
        raise SchemaError(f"{key} must be object")
    return v


def _validate_keywords_any(arr: Any, *, path: str) -> List[str]:
    if arr is None:
        return []
    if not _is_list(arr):
        raise SchemaError(f"{path} must be list")
    if len(arr) > 8:
        raise SchemaError(f"{path} too many items (max 8)")
    out: List[str] = []
    for i, x in enumerate(arr):
        if not _is_str(x):
            raise SchemaError(f"{path}[{i}] must be string")
        x2 = _sanitize_text(_strip(x))
        if not x2:
            raise SchemaError(f"{path}[{i}] empty string not allowed")
        if len(x2) > LIMITS["kw_max"]:
            raise SchemaError(f"{path}[{i}] too long (max {LIMITS['kw_max']})")
        out.append(x2)
    return out


def _validate_points(points: Any, *, path: str, min_items: int, max_items: int) -> List[Dict[str, Any]]:
    if points is None:
        points = []
    if not _is_list(points):
        raise SchemaError(f"{path} must be list")
    if not (min_items <= len(points) <= max_items):
        raise SemanticError(f"{path} must have {min_items}..{max_items} items")

    seen_ids = set()
    out: List[Dict[str, Any]] = []

    for idx, p in enumerate(points):
        if not _is_dict(p):
            raise SchemaError(f"{path}[{idx}] must be object")

        pid = p.get("id")
        if not _is_str(pid) or not _strip(pid):
            raise SchemaError(f"{path}[{idx}].id must be string")
        pid2 = _strip(pid)
        if len(pid2) > 16:
            raise SchemaError(f"{path}[{idx}].id too long (max 16)")
        if pid2 in seen_ids:
            raise SemanticError(f"{path}[{idx}].id duplicated: {pid2}")
        seen_ids.add(pid2)

        label = p.get("label")
        if not _is_str(label):
            raise SchemaError(f"{path}[{idx}].label must be string")
        label2 = _sanitize_text(_strip(label))
        if not label2:
            raise SchemaError(f"{path}[{idx}].label empty")
        if len(label2) > 40:  # 60 → 40に短縮
            raise SchemaError(f"{path}[{idx}].label too long (max 40)")

        notes = p.get("notes")
        if not _is_str(notes):
            raise SchemaError(f"{path}[{idx}].notes must be string")
        notes2 = _sanitize_text(_strip(notes))
        if not notes2:
            raise SchemaError(f"{path}[{idx}].notes empty")
        if len(notes2) > 80:  # 120 → 80に短縮
            raise SchemaError(f"{path}[{idx}].notes too long (max 80)")

        keywords_any = _validate_keywords_any(p.get("keywords_any", []), path=f"{path}[{idx}].keywords_any")
        
        # keywords_anyの最大長を20文字に制限
        keywords_any_short = []
        for kw in keywords_any:
            if len(kw) > 20:
                keywords_any_short.append(kw[:20])
            else:
                keywords_any_short.append(kw)

        out.append(
            {
                "id": pid2,
                "label": label2,
                "keywords_any": keywords_any_short,
                "notes": notes2,
            }
        )

    return out


def _validate_scoring_policy(obj: Any, must_total: int) -> Dict[str, Any]:
    if not _is_dict(obj):
        raise SchemaError("rubric.scoringPolicy must be object")

    def _num(x: Any) -> float | None:
        if isinstance(x, bool):
            return None
        if isinstance(x, (int, float)):
            return float(x)
        return None

    ct = _num(obj.get("correct_threshold"))
    cl = _num(obj.get("close_threshold"))
    if ct is None or cl is None:
        raise SchemaError("scoringPolicy thresholds must be numbers")
    if not (0.0 < cl <= 1.0):
        raise SemanticError("close_threshold must be (0,1]")
    if not (0.0 < ct <= 1.0):
        raise SemanticError("correct_threshold must be (0,1]")
    if ct < cl:
        raise SemanticError("correct_threshold must be >= close_threshold")

    must_points_total = obj.get("must_points_total")
    if not _is_int(must_points_total) or must_points_total < 1:
        raise SchemaError("must_points_total must be positive integer")
    if must_points_total != must_total:
        raise SemanticError("must_points_total must equal len(mustHavePoints)")

    close_at_least = obj.get("close_if_must_points_met_at_least")
    correct_at_least = obj.get("correct_if_must_points_met_at_least")
    if not _is_int(close_at_least) or close_at_least < 0:
        raise SchemaError("close_if_must_points_met_at_least must be integer >= 0")
    if not _is_int(correct_at_least) or correct_at_least < 0:
        raise SchemaError("correct_if_must_points_met_at_least must be integer >= 0")

    if close_at_least > must_total or correct_at_least > must_total:
        raise SemanticError("scoringPolicy *_at_least cannot exceed must points total")
    if correct_at_least < close_at_least:
        raise SemanticError("correct_if_must_points_met_at_least must be >= close_if_must_points_met_at_least")

    # enforce exact thresholds per app spec
    eps = 1e-6
    if abs(ct - 1.0) > eps:
        raise SemanticError("correct_threshold must be 1.0")
    if abs(cl - 0.8) > eps:
        raise SemanticError("close_threshold must be 0.8")

    return {
        "correct_threshold": ct,
        "close_threshold": cl,
        "must_points_total": must_points_total,
        "close_if_must_points_met_at_least": close_at_least,
        "correct_if_must_points_met_at_least": correct_at_least,
    }


# -----------------------------
# judge (answer evaluation) validator
# -----------------------------

_ALLOWED_JUDGE_RESULTS = {"correct", "close", "incorrect"}


def _require_float01(obj: Dict[str, Any], key: str) -> float:
    v = _require_key(obj, key)
    if isinstance(v, bool):
        raise SchemaError(f"{key} must be number")
    if not isinstance(v, (int, float)):
        raise SchemaError(f"{key} must be number")
    f = float(v)
    if not (0.0 <= f <= 1.0):
        raise SemanticError(f"{key} must be between 0.0 and 1.0")
    return f


def _require_str_list(obj: Dict[str, Any], key: str, *, max_items: int | None = None) -> List[str]:
    v = obj.get(key, [])
    if v is None:
        v = []
    if not _is_list(v):
        raise SchemaError(f"{key} must be list")
    if max_items is not None and len(v) > max_items:
        raise SchemaError(f"{key} too many items (max {max_items})")
    out: List[str] = []
    for i, x in enumerate(v):
        if not _is_str(x):
            raise SchemaError(f"{key}[{i}] must be string")
        x2 = _sanitize_text(_strip(x))
        if not x2:
            raise SchemaError(f"{key}[{i}] empty")
        if len(x2) > 16:
            raise SchemaError(f"{key}[{i}] too long (max 16)")
        out.append(x2)
    return out


def validate_judgment(obj: Dict[str, Any], rubric: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate Bedrock judging output JSON against rubric.
    Returns normalized dict safe to return from API.
    """
    if not _is_dict(obj):
        raise SchemaError("Judge output must be object")

    result = _require_str(obj, "result", min_len=1, max_len=16).lower()
    if result not in _ALLOWED_JUDGE_RESULTS:
        raise SemanticError("result must be correct|close|incorrect")

    score = _require_float01(obj, "score")

    must_met = _require_str_list(obj, "mustPointsMet", max_items=16)
    missing = _require_str_list(obj, "missingMustPoints", max_items=16)

    # basic dedupe + disjointness
    if len(set(must_met)) != len(must_met):
        raise SemanticError("mustPointsMet contains duplicates")
    if len(set(missing)) != len(missing):
        raise SemanticError("missingMustPoints contains duplicates")
    if set(must_met) & set(missing):
        raise SemanticError("mustPointsMet and missingMustPoints must be disjoint")

    # feedbackは超過時に切り詰める（Bedrockが守らないことがあるため）
    feedback = _require_str(obj, "feedback", min_len=1, max_len=LIMITS["feedback_max"], truncate=True)
    
    next_hint = obj.get("nextHint", "")
    if next_hint is None:
        next_hint = ""
    if not _is_str(next_hint):
        raise SchemaError("nextHint must be string")
    next_hint2 = _sanitize_text(_strip(next_hint))
    if len(next_hint2) > LIMITS["hint_max"]:
        # 超過した場合は切り詰める（エラーにせずUIを落とさない）
        next_hint2 = next_hint2[:LIMITS["hint_max"]]

    # rubric integrity checks
    if not _is_dict(rubric):
        raise SchemaError("rubric must be object")

    must_points = rubric.get("mustHavePoints")
    if not _is_list(must_points):
        raise SchemaError("rubric.mustHavePoints must be list")

    must_ids: List[str] = []
    for i, p in enumerate(must_points):
        if not _is_dict(p):
            raise SchemaError(f"rubric.mustHavePoints[{i}] must be object")
        pid = p.get("id")
        if not _is_str(pid) or not _strip(pid):
            raise SchemaError(f"rubric.mustHavePoints[{i}].id must be string")
        must_ids.append(_strip(pid))

    must_id_set = set(must_ids)
    # only allow ids that appear in rubric.mustHavePoints
    for x in must_met:
        if x not in must_id_set:
            raise SemanticError(f"mustPointsMet contains unknown id: {x}")
    for x in missing:
        if x not in must_id_set:
            raise SemanticError(f"missingMustPoints contains unknown id: {x}")

    # If model omitted some must points from both lists, compute missing to be safe
    # (keeps API stable even if model forgets some)
    all_classified = set(must_met) | set(missing)
    if len(all_classified) != len(must_id_set):
        # fill missing side
        unclassified = list(must_id_set - all_classified)
        # treat unclassified as missing (conservative)
        missing = missing + sorted(unclassified)

    # enforce scoringPolicy -> result consistency
    scoring_policy = rubric.get("scoringPolicy")
    if not _is_dict(scoring_policy):
        raise SchemaError("rubric.scoringPolicy must be object")

    must_total = len(must_id_set)
    # accept both snake_case and camelCase just in case rubric stored differs
    def _pick(scoring: Dict[str, Any], snake: str, camel: str) -> Any:
        v = scoring.get(snake)
        if v is None:
            v = scoring.get(camel)
        return v

    close_at_least = _pick(
        scoring_policy,
        "close_if_must_points_met_at_least",
        "closeIfMustPointsMetAtLeast",
    )
    correct_at_least = _pick(
        scoring_policy,
        "correct_if_must_points_met_at_least",
        "correctIfMustPointsMetAtLeast",
    )

    if not _is_int(close_at_least) or not _is_int(correct_at_least):
        raise SchemaError("rubric.scoringPolicy *_at_least must be integers")

    # normalize Decimal -> int
    close_at_least = int(close_at_least)
    correct_at_least = int(correct_at_least)

    met_count = len(set(must_met))
    expected_result: str
    if met_count >= correct_at_least:
        expected_result = "correct"
    elif met_count >= close_at_least:
        expected_result = "close"
    else:
        expected_result = "incorrect"

    if result != expected_result:
        # 実運用ではエラーにせず補正（UIを落とさない）
        result = expected_result

        # scoreも最低限 must充足率以上に補正（niceToHaveで上振れは許容）
        base = met_count / max(1, must_total)
        if score < base:
            score = base

    # keep score consistent-ish: at least met_count/must_total (niceToHave may add up to 0.1)
    base = met_count / max(1, must_total)

    # Bedrockがscoreを低く/高く返すことがあるため、ここではエラーにせず補正する
    if score < base:
        score = base

    # 0.0〜1.0 にクリップ
    if score > 1.0:
        score = 1.0
    if score < 0.0:
        score = 0.0

    # normalize outputs
    return {
        "result": result,
        "score": float(min(1.0, max(0.0, score))),
        "mustPointsMet": sorted(set(must_met)),
        "missingMustPoints": sorted(set(missing)),
        "feedback": feedback,
        "nextHint": next_hint2,
    }


# Backward compatibility for british spelling
validate_judgement = validate_judgment


# -----------------------------
# rubric validator (for separate rubric generation)
# -----------------------------

def validate_rubric(obj: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate rubric-only JSON output from Bedrock.
    Returns normalized rubric dict.
    """
    if not _is_dict(obj):
        raise SchemaError("Rubric output must be object")

    expected_answer = _require_str(
        obj,
        "expectedAnswer",
        min_len=1,
        max_len=LIMITS["expected_answer_max"],
    )

    must_points_raw = _require_list(obj, "mustHavePoints")
    must_points = _validate_points(must_points_raw, path="mustHavePoints", min_items=4, max_items=6)

    nice_points_raw = obj.get("niceToHavePoints", [])
    nice_points = _validate_points(nice_points_raw, path="niceToHavePoints", min_items=0, max_items=6) if nice_points_raw else []

    wrong_claims_raw = obj.get("commonWrongClaims", [])
    wrong_claims = _validate_points(wrong_claims_raw, path="commonWrongClaims", min_items=0, max_items=6) if wrong_claims_raw else []

    scoring_policy = _validate_scoring_policy(_require_key(obj, "scoringPolicy"), must_total=len(must_points))

    return {
        "expectedAnswer": expected_answer,
        "mustHavePoints": must_points,
        "niceToHavePoints": nice_points,
        "commonWrongClaims": wrong_claims,
        "scoringPolicy": scoring_policy,
    }


# -----------------------------
# main validator (question generation)
# -----------------------------

def validate_generation(obj: Dict[str, Any]) -> Dict[str, Any]:
    title = _require_str(obj, "title", min_len=1, max_len=LIMITS["title_max"])
    body = _require_str(obj, "body", min_len=1, max_len=LIMITS["body_max"])

    category = _require_str(obj, "category", min_len=1, max_len=32)
    if category not in ALLOWED_CATEGORIES:
        raise SemanticError(f"category not allowed: {category}")

    level = _require_int(obj, "level")
    if level not in ALLOWED_LEVELS:
        raise SemanticError(f"level not allowed: {level}")

    rubric = _require_dict(obj, "rubric")

    expected_answer = _require_str(
        rubric,
        "expectedAnswer",
        min_len=1,
        max_len=LIMITS["expected_answer_max"],
    )

    must_points_raw = _require_list(rubric, "mustHavePoints")
    must_points = _validate_points(must_points_raw, path="rubric.mustHavePoints", min_items=4, max_items=6)

    nice_points_raw = rubric.get("niceToHavePoints", [])
    nice_points = _validate_points(nice_points_raw, path="rubric.niceToHavePoints", min_items=0, max_items=6) if nice_points_raw else []

    wrong_claims_raw = rubric.get("commonWrongClaims", [])
    wrong_claims = _validate_points(wrong_claims_raw, path="rubric.commonWrongClaims", min_items=0, max_items=6) if wrong_claims_raw else []

    scoring_policy = _validate_scoring_policy(_require_key(rubric, "scoringPolicy"), must_total=len(must_points))

    source_summary = _require_str(obj, "sourceSummary", min_len=1, max_len=LIMITS["source_summary_max"])

    tags_raw = obj.get("tags", [])
    if tags_raw is None:
        tags_raw = []
    if not _is_list(tags_raw):
        raise SchemaError("tags must be list")
    if len(tags_raw) > 20:
        raise SchemaError("tags too many items (max 20)")

    tags: List[str] = []
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
        "rubric": {
            "expectedAnswer": expected_answer,
            "mustHavePoints": must_points,
            "niceToHavePoints": nice_points,
            "commonWrongClaims": wrong_claims,
            "scoringPolicy": scoring_policy,
        },
        "sourceSummary": source_summary,
        "tags": tags,
    }
