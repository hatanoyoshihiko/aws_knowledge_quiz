from __future__ import annotations
import re
import unicodedata
import hashlib

_ws_re = re.compile(r"\s+")
_ctl_re = re.compile(r"[\x00-\x1F\x7F]")

def norm(s: str) -> str:
    if not isinstance(s, str):
        raise TypeError("norm() expects str")
    if _ctl_re.search(s):
        raise ValueError("control characters not allowed")
    s = unicodedata.normalize("NFKC", s)
    s = s.strip()
    s = _ws_re.sub(" ", s)
    # punctuation unification
    s = s.replace("，", ",").replace("．", ".")
    s = s.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    # lowercase only ASCII letters
    s = "".join(ch.lower() if "A" <= ch <= "Z" else ch for ch in s)
    return s

def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def canonical_question_string(
    *,
    category: str,
    level: int,
    title: str,
    body: str,
    must_points: list[dict],
    language: str = "ja",
    version: str = "v1",
) -> str:
    parts: list[str] = []
    parts.append(version)
    parts.append(f"lang={norm(language)}")
    parts.append(f"cat={norm(category)}")
    parts.append(f"level={level}")
    parts.append(f"title={norm(title)}")
    parts.append(f"body={norm(body)}")

    # must points sorted by id
    must_sorted = sorted(must_points, key=lambda x: x.get("id", ""))
    for mp in must_sorted:
        mid = mp["id"]
        label = norm(mp["label"])
        kws = [norm(k) for k in mp["keywords_any"]]
        kws = sorted(set(kws))
        parts.append(f"must={mid}:{label}:[{','.join(kws)}]")

    return "|".join(parts)

def question_hash(
    *,
    category: str,
    level: int,
    title: str,
    body: str,
    must_points: list[dict],
) -> str:
    canon = canonical_question_string(
        category=category, level=level, title=title, body=body, must_points=must_points
    )
    return sha256_hex(canon)
