from goldirham_filings.retrieve import Filters, balance_groups, interleave, prefer_recent


def test_interleave_takes_each_rankings_best_first_without_repeats() -> None:
    assert interleave([[1, 2, 3, 4], [9, 2, 8], [7]]) == [1, 9, 7, 2, 3, 8, 4]


def test_balance_groups_split_only_several_named_filings() -> None:
    two = Filters(tickers=["MSFT"], accessions=["a", "b"])
    assert [g.accessions for g in balance_groups(two)] == [["a"], ["b"]]
    assert all(g.tickers == ["MSFT"] for g in balance_groups(two))
    assert balance_groups(Filters(accessions=["a"])) == [Filters(accessions=["a"])]
    assert balance_groups(Filters(tickers=["MSFT"])) == [Filters(tickers=["MSFT"])]


def test_prefer_recent_moves_a_newer_filing_up_about_two_places_per_step() -> None:
    ids = [1, 2, 3]
    assert prefer_recent(ids, {1: 1, 2: 0, 3: 0}, decay=0.97) == [2, 1, 3]
    assert prefer_recent(ids, {1: 3, 2: 0, 3: 0}, decay=0.97) == [2, 3, 1]
    assert prefer_recent(ids, {1: 0, 2: 2, 3: 0}, decay=1.0) == [1, 2, 3]
