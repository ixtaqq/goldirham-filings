import json
from datetime import date
from typing import Any

import pytest

from goldirham_filings.answer import AnswerResult, CitedSentence
from goldirham_filings.evaluate import (
    ThresholdChoice,
    dated_decline,
    gate,
    grade_dated_decline,
    score_answer,
    score_retrieval,
    similarity_threshold,
    summarize_answers,
    summarize_retrieval,
)
from goldirham_filings.golden import (
    AlternateSpan,
    GoldItem,
    GoldSpan,
    ItemType,
    make_split,
    mark_alternates,
    select,
)
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


LAM_QUESTION = "How many regular full-time employees did Lam have as of December 31, 2026?"
NOT_REPORTED = "The filings do not report headcount as of December 31, 2026."
DATED = "As of August 4, 2026, Lam had about 23,300 regular full-time employees."
UNANSWERABLE = GoldItem(
    id="u", type="unanswerable", question=LAM_QUESTION, answerable=False, gold_spans=[],
    gold_answer="",
)  # fmt: skip


class Judge:
    def __init__(self, passes: bool) -> None:
        self.passes = passes
        self.calls = 0

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> str:
        self.calls += 1
        return json.dumps({"passes": self.passes})


def answered(*texts: str) -> AnswerResult:
    sentences = [CitedSentence(text=t, citations=[1]) for t in texts]
    return AnswerResult(
        status="answered", question="q", sentences=sentences, passages=[passage("A", "1A", 0, 10)]
    )


def test_saying_not_reported_and_giving_other_dates_is_a_decline() -> None:
    assert dated_decline(LAM_QUESTION, [NOT_REPORTED, DATED])


def test_dated_figures_without_saying_not_reported_are_not_a_decline() -> None:
    # heldout-unanswerable-003's actual answer: dated figures, no statement of absence.
    assert not dated_decline(LAM_QUESTION, [DATED])


def test_presenting_a_figure_as_the_asked_period_is_not_a_decline() -> None:
    claim = "As of December 31, 2026, Lam had about 23,300 regular full-time employees."
    assert not dated_decline(LAM_QUESTION, [NOT_REPORTED, claim])


def test_an_undated_figure_is_not_a_decline() -> None:
    assert not dated_decline(LAM_QUESTION, [NOT_REPORTED, "Lam had about 23,300 employees."])


def test_a_question_naming_no_period_cannot_be_a_dated_decline() -> None:
    assert not dated_decline("How many employees does Lam have?", [NOT_REPORTED, DATED])


def test_the_judge_confirms_only_what_the_rule_passes() -> None:
    agrees, disagrees, unused = Judge(True), Judge(False), Judge(True)
    assert grade_dated_decline(agrees, UNANSWERABLE, answered(NOT_REPORTED, DATED))
    assert not grade_dated_decline(disagrees, UNANSWERABLE, answered(NOT_REPORTED, DATED))
    assert not grade_dated_decline(unused, UNANSWERABLE, answered(DATED))
    assert (agrees.calls, disagrees.calls, unused.calls) == (1, 1, 0)


def test_dated_declines_have_their_own_metric() -> None:
    plain = score_answer(UNANSWERABLE, answered(DATED), "not_applicable")
    graded = score_answer(UNANSWERABLE, answered(NOT_REPORTED, DATED), "not_applicable", True)
    summary = summarize_answers([plain, graded])
    assert summary["abstention_accuracy"] == 0.0
    assert summary["abstention_accuracy_with_dated_declines"] == 0.5


REVIEWED_ON = date(2026, 1, 2)


def with_alternates() -> list[GoldItem]:
    spans = [
        AlternateSpan(accession="A", section="7", quote="q", start=1, end=2, for_span=n)
        for n in range(2)
    ]
    return [ITEM.model_copy(update={"alternate_spans": spans})]


def test_marking_an_alternate_sets_only_that_span() -> None:
    marked = mark_alternates(with_alternates(), [("comparison-001", 1)], "A Person", REVIEWED_ON)
    first, second = marked[0].alternate_spans
    assert (first.checked_by, first.checked_at) == (None, None)
    assert (second.checked_by, second.checked_at) == ("A Person", REVIEWED_ON)
    assert marked[0].checked_by is None


def test_marking_an_unknown_alternate_is_refused() -> None:
    for ref in [("comparison-001", 2), ("missing", 0)]:
        with pytest.raises(ValueError, match="no alternate span"):
            mark_alternates(with_alternates(), [ref], "A Person", REVIEWED_ON)


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
    assert summary == {
        "items": 2, "recall_at_5": 0.5, "recall_at_8": 0.5, "recall_at_10": 0.5, "mrr": 0.5,
        "recall_at_10_by_type": {"comparison": 0.5},
    }  # fmt: skip
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


def test_split_is_stratified_reproducible_and_disjoint() -> None:
    counts: tuple[tuple[ItemType, int], ...] = (("lookup", 21), ("risk", 15), ("unanswerable", 9))
    items = [
        GoldItem(
            id=f"{t}-{n:03d}",
            type=t,
            question="q",
            answerable=t != "unanswerable",
            gold_spans=[],
            gold_answer="",
        )  # fmt: skip
        for t, count in counts
        for n in range(count)
    ]
    split = make_split(items, seed=7, test_fraction=1 / 3)
    assert split == make_split(list(reversed(items)), seed=7, test_fraction=1 / 3)
    assert not set(split["dev"]) & set(split["test"])
    assert [sum(i.startswith(t) for i in split["test"]) for t in ("lookup", "risk", "unan")] == [
        7, 5, 3,
    ]  # fmt: skip
    assert [i.id for i in select(items, split, "test")] == split["test"]


def test_alternate_spans_count_only_when_checked_in_the_headline_metric() -> None:
    restated = AlternateSpan(accession="C", section="1A", quote="q", start=10, end=40, for_span=0)
    result = AnswerResult(
        status="answered", question="q",
        sentences=[CitedSentence(text="s", citations=[1, 2])],
        passages=[passage("A", "1A", 90, 120), passage("C", "1A", 0, 50)],
    )  # fmt: skip
    unchecked = ITEM.model_copy(update={"alternate_spans": [restated]})
    score = score_answer(unchecked, result, "correct")
    assert (score.citation_precision, score.citation_precision_unchecked_alternates) == (0.5, 1.0)
    owner_checked = restated.model_copy(
        update={"checked_by": "owner", "checked_at": date(2026, 11, 7)}
    )
    checked = ITEM.model_copy(update={"alternate_spans": [owner_checked]})
    assert score_answer(checked, result, "correct").citation_precision == 1.0
