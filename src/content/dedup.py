"""Deduplication and near-duplicate detection.

Three layers, increasingly semantic:
  1. exact          — identical normalized text (via content_hash, done in DB)
  2. fuzzy          — RapidFuzz token_sort_ratio on normalized text
  3. semantic-ish   — cosine over char-n-gram + word features (paraphrase catch)

No embeddings model required; if ``sentence-transformers`` is installed it can be
plugged in later, but the default is dependency-light and deterministic.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from rapidfuzz import fuzz

from .normalize import normalize_text


def _features(norm_text: str) -> Counter:
    """Bag of word-unigrams, word-bigrams and char-trigrams."""
    feats: Counter = Counter()
    words = norm_text.split()
    for w in words:
        feats["w:" + w] += 1
    for a, b in zip(words, words[1:]):
        feats["b:" + a + "_" + b] += 1
    s = "^" + norm_text.replace(" ", "_") + "$"
    for i in range(len(s) - 2):
        feats["c:" + s[i : i + 3]] += 1
    return feats


def _cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    # iterate the smaller for the dot product
    small, large = (a, b) if len(a) <= len(b) else (b, a)
    dot = sum(v * large.get(k, 0) for k, v in small.items())
    if dot == 0:
        return 0.0
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb)


@dataclass
class Match:
    id: int | None
    kind: str  # "exact" | "fuzzy" | "semantic" | "none"
    score: float  # 0..1 (fuzzy scores are /100)


class SimilarityIndex:
    """Incremental index for near-duplicate detection and clustering."""

    def __init__(self, *, fuzzy_threshold: float = 0.88, semantic_threshold: float = 0.82):
        self.fuzzy_threshold = fuzzy_threshold
        self.semantic_threshold = semantic_threshold
        self._items: list[tuple[int, str, Counter]] = []

    def __len__(self) -> int:
        return len(self._items)

    def add(self, item_id: int, text: str) -> None:
        norm = normalize_text(text)
        self._items.append((item_id, norm, _features(norm)))

    @staticmethod
    def _length_compatible(a: str, b: str, threshold: float) -> bool:
        """Cheap pre-filter: texts of very different length cannot be near-dups.

        ``token_sort_ratio`` is bounded above by ``2*min/(len_a+len_b)``, so a
        pair failing that bound can be skipped without scoring it. This keeps the
        O(n²) sweep practical on 1.000-item datasets.
        """
        la, lb = len(a), len(b)
        if not la or not lb:
            return False
        return (2.0 * min(la, lb)) / (la + lb) >= threshold * 0.85

    def best_match(self, text: str) -> Match:
        """Return the most similar existing item (fuzzy or semantic)."""
        norm = normalize_text(text)
        feats = _features(norm)
        best = Match(id=None, kind="none", score=0.0)
        floor = min(self.fuzzy_threshold, self.semantic_threshold)
        for item_id, other_norm, other_feats in self._items:
            if norm == other_norm:
                return Match(id=item_id, kind="exact", score=1.0)
            if not self._length_compatible(norm, other_norm, floor):
                continue
            fz = fuzz.token_sort_ratio(norm, other_norm) / 100.0
            sem = _cosine(feats, other_feats)
            # pick whichever signal is strongest for this pair
            if fz >= sem and fz > best.score:
                best = Match(id=item_id, kind="fuzzy", score=round(fz, 4))
            elif sem > best.score:
                best = Match(id=item_id, kind="semantic", score=round(sem, 4))
        return best

    def is_duplicate(self, text: str) -> tuple[bool, Match]:
        m = self.best_match(text)
        dup = (
            (m.kind == "exact")
            or (m.kind == "fuzzy" and m.score >= self.fuzzy_threshold)
            or (m.kind == "semantic" and m.score >= self.semantic_threshold)
            or (m.kind == "fuzzy" and m.score >= self.semantic_threshold)
        )
        return dup, m

    # -- clustering ---------------------------------------------------------
    def cluster(self, threshold: float | None = None) -> dict[int, int]:
        """Union-find clustering. Returns {item_id: cluster_id}.

        Two items join a cluster if their fuzzy OR semantic similarity exceeds
        ``threshold`` (defaults to ``semantic_threshold``).
        """
        thr = self.semantic_threshold if threshold is None else threshold
        n = len(self._items)
        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x: int, y: int) -> None:
            parent[find(x)] = find(y)

        for i in range(n):
            _, ni, fi = self._items[i]
            for j in range(i + 1, n):
                _, nj, fj = self._items[j]
                if not self._length_compatible(ni, nj, thr):
                    continue
                fz = fuzz.token_sort_ratio(ni, nj) / 100.0
                if fz >= thr or _cosine(fi, fj) >= thr:
                    union(i, j)

        # map root index -> stable cluster id (the smallest item_id in the group)
        groups: dict[int, list[int]] = {}
        for idx in range(n):
            groups.setdefault(find(idx), []).append(idx)
        result: dict[int, int] = {}
        for members in groups.values():
            cluster_id = min(self._items[m][0] for m in members)
            for m in members:
                result[self._items[m][0]] = cluster_id
        return result
