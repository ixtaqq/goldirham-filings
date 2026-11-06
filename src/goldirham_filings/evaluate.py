"""Metrics over the golden set. Scoring functions are pure; runners call the real system.

Retrieval (deterministic): Recall@5, Recall@10 and MRR over answerable items. A retrieved
chunk hits a gold span when both are in the same filing section and their offsets overlap.
Answers: citation precision, numbers missing from cited passages, abstention accuracy and
false abstention, plus answer agreement from a local model judge (indicative only).
"""

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from statistics import mean
from typing import Any, Literal

from pydantic import BaseModel

from goldirham_filings.answer import Answerer, AnswerResult, Chat
from goldirham_filings.golden import GoldItem, GoldSpan
from goldirham_filings.retrieve import Passage, Retriever

GATE_TOLERANCE = 0.02
GATED_METRICS = ("recall_at_10", "mrr")


def span_hit(passage: Passage, span: GoldSpan) -> bool:
    assert span.start is not None and span.end is not None
    return (
        passage.accession == span.accession
        and passage.section == span.section
        and passage.start_char < span.end
        and span.start < passage.end_char
    )


@dataclass(frozen=True)
class RetrievalScore:
    item_id: str
    item_type: str
    recall_at_5: float
    recall_at_8: float
    recall_at_10: float
    reciprocal_rank: float
    top_similarity: float | None


def score_retrieval(item: GoldItem, ranked: Sequence[Passage]) -> RetrievalScore:
    """Unanswerable items have no gold spans; they score 0 recall and only their
    top_similarity is used, for the abstention threshold."""

    def recall(k: int) -> float:
        if not item.gold_spans:
            return 0.0
        top = ranked[:k]
        return sum(any(span_hit(p, s) for p in top) for s in item.gold_spans) / len(item.gold_spans)

    first_hit = next(
        (rank for rank, p in enumerate(ranked, 1) if any(span_hit(p, s) for s in item.gold_spans)),
        None,
    )
    similarities = [p.similarity for p in ranked if p.similarity is not None]
    return RetrievalScore(
        item_id=item.id,
        item_type=item.type,
        recall_at_5=recall(5),
        recall_at_8=recall(8),
        recall_at_10=recall(10),
        reciprocal_rank=1.0 / first_hit if first_hit else 0.0,
        top_similarity=max(similarities) if similarities else None,
    )


def summarize_retrieval(scores: Sequence[RetrievalScore]) -> dict[str, Any]:
    """Overall metrics, plus Recall@10 per item type. recall_at_8 is what the answerer sees."""
    types = sorted({s.item_type for s in scores})
    return {
        "items": len(scores),
        "recall_at_5": round(mean(s.recall_at_5 for s in scores), 4),
        "recall_at_8": round(mean(s.recall_at_8 for s in scores), 4),
        "recall_at_10": round(mean(s.recall_at_10 for s in scores), 4),
        "mrr": round(mean(s.reciprocal_rank for s in scores), 4),
        "recall_at_10_by_type": {
            t: round(mean(s.recall_at_10 for s in scores if s.item_type == t), 4) for t in types
        },
    }


def run_retrieval(
    retriever: Retriever, items: Sequence[GoldItem], k: int = 10
) -> list[RetrievalScore]:
    return [
        score_retrieval(item, retriever.search(item.question, item.filters, k=k)) for item in items
    ]


def gate(current: dict[str, float], baseline: dict[str, float]) -> list[str]:
    """Failures: each gated metric more than GATE_TOLERANCE below its baseline."""
    return [
        f"{m} {current[m]:.4f} is more than {GATE_TOLERANCE} below baseline {baseline[m]:.4f}"
        for m in GATED_METRICS
        if current[m] < baseline[m] - GATE_TOLERANCE
    ]


@dataclass(frozen=True)
class ThresholdChoice:
    threshold: float
    false_abstention: float
    unanswerable_caught: float


def similarity_threshold(
    answerable: Sequence[float], unanswerable: Sequence[float], max_false_abstention: float = 0.05
) -> ThresholdChoice:
    """Highest top-similarity cut that abstains on at most max_false_abstention of the
    answerable items, and the share of unanswerable items it would catch on its own.
    The cut is the lowest answerable similarity allowed through, so ties are kept."""
    if not answerable:
        return ThresholdChoice(0.0, 0.0, 0.0)
    ordered = sorted(answerable)
    cut = ordered[min(int(max_false_abstention * len(ordered)), len(ordered) - 1)]
    return ThresholdChoice(
        threshold=cut,
        false_abstention=sum(s < cut for s in ordered) / len(ordered),
        unanswerable_caught=(
            sum(s < cut for s in unanswerable) / len(unanswerable) if unanswerable else 0.0
        ),
    )


Verdict = Literal["correct", "partial", "incorrect", "not_applicable"]


class JudgeVerdict(BaseModel):
    verdict: Literal["correct", "partial", "incorrect"]
    reason: str


JUDGE_PROMPT = """Compare a candidate answer with the reference answer to a question about an
SEC filing. "correct": the candidate states the reference's key facts and nothing that
contradicts it. "partial": some key facts, or extra unsupported claims. "incorrect": wrong,
missing, or contradicts the reference."""


@dataclass(frozen=True)
class AnswerScore:
    item_id: str
    item_type: str
    answerable: bool
    abstained: bool
    citation_precision: float | None
    unsupported_numbers: int
    unsupported_periods: int
    verdict: Verdict
    answer: str
    gold_in_passages: bool
    cited: list[str]
    notes: list[str]


def _where(p: Passage) -> str:
    return f"{p.accession} {p.section} {p.start_char}-{p.end_char}"


def score_answer(item: GoldItem, result: AnswerResult, verdict: Verdict) -> AnswerScore:
    abstained = result.status == "abstained"
    cited = sorted({c for s in result.sentences for c in s.citations})
    precision = None
    if cited and item.gold_spans:
        hits = [any(span_hit(result.passages[c - 1], s) for s in item.gold_spans) for c in cited]
        precision = sum(hits) / len(hits)
    return AnswerScore(
        item_id=item.id,
        item_type=item.type,
        answerable=item.answerable,
        abstained=abstained,
        citation_precision=precision,
        unsupported_numbers=sum(len(s.unsupported_numbers) for s in result.sentences),
        unsupported_periods=sum(len(s.unsupported_periods) for s in result.sentences),
        verdict=verdict,
        answer=" ".join(s.text for s in result.sentences),
        gold_in_passages=any(span_hit(p, s) for p in result.passages for s in item.gold_spans),
        cited=[_where(result.passages[c - 1]) for c in cited],
        notes=result.notes,
    )


def summarize_answers(scores: Sequence[AnswerScore]) -> dict[str, Any]:
    answerable = [s for s in scores if s.answerable]
    unanswerable = [s for s in scores if not s.answerable]
    precisions = [s.citation_precision for s in answerable if s.citation_precision is not None]
    judged = [s for s in answerable if s.verdict != "not_applicable"]
    declined = [s for s in answerable if s.abstained]
    return {
        "items": len(scores),
        "declines_by_type": {
            t: f"{sum(s.abstained for s in scores if s.item_type == t)}"
            f"/{sum(s.item_type == t for s in scores)}"
            for t in sorted({s.item_type for s in scores})
        },
        "false_declines_with_gold_in_passages": sum(s.gold_in_passages for s in declined),
        "citation_precision": round(mean(precisions), 4) if precisions else None,
        "answers_with_unsupported_numbers": sum(s.unsupported_numbers > 0 for s in scores),
        "answers_with_unsupported_periods": sum(s.unsupported_periods > 0 for s in scores),
        "abstention_accuracy": (
            round(mean(s.abstained for s in unanswerable), 4) if unanswerable else None
        ),
        "false_abstention_rate": (
            round(mean(s.abstained for s in answerable), 4) if answerable else None
        ),
        "judge_correct_rate": (
            round(mean(s.verdict == "correct" for s in judged), 4) if judged else None
        ),
        "judge_note": "local 8B judge; indicative until compared with a human spot-check",
    }


def judge(chat: Chat, item: GoldItem, answer: str) -> Verdict:
    prompt = f"Question: {item.question}\nReference: {item.gold_answer}\nCandidate: {answer}"
    raw = chat.complete_json(JUDGE_PROMPT, prompt, JudgeVerdict.model_json_schema())
    return JudgeVerdict.model_validate_json(raw).verdict


def run_answers(
    answerer: Answerer, judge_chat: Chat, items: Sequence[GoldItem]
) -> list[AnswerScore]:
    scores = []
    for item in items:
        result = answerer.answer(item.question, item.filters)
        verdict: Verdict = "not_applicable"
        if item.answerable and result.status == "answered":
            verdict = judge(judge_chat, item, " ".join(s.text for s in result.sentences))
        elif item.answerable:
            verdict = "incorrect"
        scores.append(score_answer(item, result, verdict))
        print(f"{item.id}: {result.status} {verdict}")
    return scores


def rows(scores: Sequence[RetrievalScore] | Sequence[AnswerScore]) -> list[dict[str, Any]]:
    return [asdict(s) for s in scores]
