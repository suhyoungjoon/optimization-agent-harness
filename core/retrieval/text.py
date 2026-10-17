"""키워드 검색: 토큰화와 BM25. 한글은 조사가 붙어 단어가 달라지므로 두 글자 조각도 토큰으로 쓴다."""

import math
import re
from collections import Counter

_TOKEN = re.compile(r"[0-9a-z_.\[\]]+|[가-힣]+")


def tokenize(text: str) -> list[str]:
    out = []
    for tok in _TOKEN.findall(text.lower()):
        out.append(tok)
        if re.fullmatch(r"[가-힣]+", tok) and len(tok) > 2:
            out += [tok[i:i + 2] for i in range(len(tok) - 1)]
    return out


def bm25_scores(query: str, docs: list[str], k1: float = 1.5, b: float = 0.75) -> list[float]:
    """docs마다 점수 (질의 토큰이 하나도 없으면 0)."""
    toks = [tokenize(d) for d in docs]
    if not toks:
        return []
    avg = sum(len(t) for t in toks) / len(toks) or 1.0
    df = Counter(term for t in toks for term in set(t))
    n = len(toks)
    q = set(tokenize(query))
    scores = []
    for t in toks:
        tf, length, s = Counter(t), len(t), 0.0
        for term in q:
            if tf[term]:
                idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
                s += idf * tf[term] * (k1 + 1) / (tf[term] + k1 * (1 - b + b * length / avg))
        scores.append(s)
    return scores
