"""The CI retrieval gate passes on the real retriever and fails on a degraded one."""

from collections.abc import Sequence

import pytest

from goldirham_filings import cli, retrieve

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def fixture_database(fixture_conn: object) -> None:
    """Skip unless TEST_DATABASE_URL points at the loaded fixture corpus."""


def test_gate_passes_on_the_real_retriever(capsys: pytest.CaptureFixture[str]) -> None:
    cli.main(["eval", "gate"])
    assert capsys.readouterr().out.splitlines()[-1] == "gate passed against baseline.json"


def test_gate_fails_when_vector_search_is_dropped(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    original = retrieve.reciprocal_rank_fusion

    def keyword_only(rankings: Sequence[Sequence[int]], k: int = retrieve.RRF_K) -> list[int]:
        return original(rankings[:1], k)

    monkeypatch.setattr(retrieve, "reciprocal_rank_fusion", keyword_only)
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["eval", "gate"])
    assert exit_info.value.code == 1
    failures = capsys.readouterr().out.splitlines()[1:]
    assert [line.split()[0] for line in failures] == ["recall_at_10", "mrr"]
