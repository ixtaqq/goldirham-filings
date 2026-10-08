from datetime import date

from goldirham_filings.answer import AnswerResult, CitedSentence
from goldirham_filings.evaluate import (
    ThresholdChoice,
    gate,
    score_answer,
    score_retrieval,
    similarity_threshold,
    summarize_retrieval,
)
from goldirham_filings.golden import GoldItem, GoldSpan
from goldirham_filings.retrieve import Passage


def passage(accession: str, section: str, start: int, end: int, sim: float = 0.5) -> Passage:
    return Passage(
        chunk_id=start, ticker="MU", company="Micron", form="10-K", accession=accession,
        period_end=date(2025, 8, 28), filed_at=date(2025, 10, 3), section=section,
        section_title="Risk Factors", start_char=start, end_char=end, text="x",
        source_url="u", similarity=sim,
    )  # fmt: skip


ITEM = GoldItem(
    id="comparison-001",
    type="comparison",
    question="q",
    answerable=True,
    gold_spans=[
        GoldSpan(accession="A", section="1A", quote="q", start=100, end=150),
        GoldSpan(accession="B", section="1A", quote="q", start=900, end=950),
    ],
    gold_answer="a",
)


def test_score_retrieval_counts_overlapping_spans_by_rank() -> None:
    ranked = [passage("A", "7", 100, 150)] * 2 + [passage("A", "1A", 0, 101, sim=0.7)]
    ranked += [passage("Z", "1A", 0, 10)] * 3 + [passage("B", "1A", 950, 990)]
    ranked += [passage("B", "1A", 940, 951)]
    score = score_retrieval(ITEM, ranked)
    assert (score.recall_at_5, score.recall_at_10, score.reciprocal_rank) == (0.5, 1.0, 1 / 3)
    assert score.top_similarity == 0.7


def test_summary_and_gate_flag_only_drops_beyond_tolerance() -> None:
    ranked = [passage("A", "1A", 120, 130), passage("B", "1A", 900, 901)]
    summary = summarize_retrieval([score_retrieval(ITEM, ranked), score_retrieval(ITEM, [])])
    assert summary == {"items": 2, "recall_at_5": 0.5, "recall_at_10": 0.5, "mrr": 0.5}
    assert gate(summary, {"recall_at_10": 0.52, "mrr": 0.5}) == []
    assert gate(summary, {"recall_at_10": 0.53, "mrr": 0.5}) == [
        "recall_at_10 0.5000 is more than 0.02 below baseline 0.5300"
    ]


def test_similarity_threshold_keeps_false_abstention_within_budget() -> None:
    answerable = [0.30, 0.55, 0.60, 0.62, 0.70, 0.71, 0.72, 0.73, 0.74, 0.75] * 2
    assert similarity_threshold(answerable, [0.2, 0.4]) == ThresholdChoice(0.30, 0.0, 0.5)
    assert similarity_threshold(answerable, [0.2, 0.4, 0.6], max_false_abstention=0.1) == (
        ThresholdChoice(0.55, 0.1, 2 / 3)
    )


def test_score_answer_citation_precision_against_gold_spans() -> None:
    result = AnswerResult(
        status="answered", question="q",
        sentences=[CitedSentence(text="s", citations=[1, 2], unsupported_numbers=["7"])],
        passages=[passage("A", "1A", 90, 120), passage("C", "1A", 0, 50)],
    )  # fmt: skip
    score = score_answer(ITEM, result, "partial")
    assert (score.citation_precision, score.unsupported_numbers, score.abstained) == (0.5, 1, False)


def test_unanswerable_item_scores_only_top_similarity() -> None:
    item = GoldItem(id="unanswerable-001", type="unanswerable", question="q", answerable=False,
                    gold_spans=[], gold_answer="")  # fmt: skip
    score = score_retrieval(item, [passage("A", "1A", 0, 10, sim=0.42)])
    assert (score.recall_at_10, score.reciprocal_rank, score.top_similarity) == (0.0, 0.0, 0.42)
