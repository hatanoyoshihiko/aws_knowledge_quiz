from __future__ import annotations
import math
import re

ALLOWED_CATEGORIES = {"security", "networking", "storage", "serverless", "well-architected"}
ALLOWED_LEVELS = {100, 200, 300, 400}

ID_P_RE = re.compile(r"^p[1-9][0-9]?$")
ID_N_RE = re.compile(r"^n[1-9][0-9]?$")
ID_W_RE = re.compile(r"^w[1-9][0-9]?$")

LIMITS = {
    "raw_json_max": 32768,          # generation raw
    "raw_json_max_judge": 16384,    # judge raw
    "title_max": 80,
    "body_min": 20,
    "body_max": 600,
    "source_summary_min": 20,
    "source_summary_max": 400,
    "expected_answer_min": 30,
    "expected_answer_max": 800,
    "tags_min": 1,
    "tags_max": 10,
    "tag_max": 30,
    "must_min": 4,
    "must_max": 6,
    "nice_max": 4,
    "wrong_max": 4,
    "label_min": 5,
    "label_max": 80,
    "keywords_min": 1,
    "keywords_max": 12,
    "kw_max": 30,
    "notes_max": 200,
    "feedback_max": 200,
    "hint_max": 120,
}

def close_threshold_for(must_total: int) -> int:
    return math.ceil(must_total * 0.8)
