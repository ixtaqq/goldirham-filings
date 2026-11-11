from goldirham_filings.doctor import (
    EXAMPLE_USER_AGENT,
    Check,
    embedding_check,
    embedding_model_check,
    format_report,
    migration_check,
    model_check,
    model_pulled,
    tickers_check,
    user_agent_check,
)


def test_user_agent_must_be_a_real_contact() -> None:
    assert not user_agent_check("").ok
    assert not user_agent_check(EXAMPLE_USER_AGENT).ok
    assert not user_agent_check("Jane Doe jane@example.com").ok
    assert not user_agent_check("Jane Doe").ok
    assert user_agent_check("Jane Doe jane@doe.dev").ok


def test_migrations_pass_only_at_head() -> None:
    assert migration_check({"0002"}, {"0002"}).ok
    behind = migration_check({"0001"}, {"0002"})
    assert not behind.ok and "upgrade head" in behind.detail
    assert "none applied" in migration_check(set(), {"0002"}).detail


def test_untagged_model_matches_latest_tag_only() -> None:
    pulled = ["nomic-embed-text:latest", "qwen3:8b"]
    assert model_pulled("nomic-embed-text", pulled)
    assert model_pulled("qwen3:8b", pulled)
    assert not model_pulled("qwen3:14b", pulled)
    assert not model_pulled("qwen3", pulled)
    missing = model_check("answer", "qwen3:14b", pulled)
    assert not missing.ok and "ollama pull qwen3:14b" in missing.detail


def test_embedding_model_must_be_supported_and_pulled() -> None:
    assert embedding_model_check("nomic-embed-text", ["nomic-embed-text:latest"]).ok
    assert not embedding_model_check("nomic-embed-text", []).ok
    unsupported = embedding_model_check("all-minilm", ["all-minilm:latest"])
    assert not unsupported.ok
    assert "all-minilm" in unsupported.detail and "nomic-embed-text" in unsupported.detail


def test_embeddings_pass_only_when_every_chunk_has_a_vector() -> None:
    assert embedding_check("m", 10, 10).ok
    partial = embedding_check("m", 9, 10)
    assert not partial.ok and "9/10 chunks (90.0%)" in partial.detail
    empty = embedding_check("m", 0, 0)
    assert not empty.ok and "ingest" in empty.detail


def test_report_has_one_verdict_per_line() -> None:
    report = format_report([Check("a", True, "fine"), Check("b", False, "broken")])
    assert report.splitlines() == ["PASS  a: fine", "FAIL  b: broken"]


def test_ingest_tickers_must_be_tracked_domestic_filers() -> None:
    assert tickers_check(["NVDA", "AMD"]).ok
    assert not tickers_check([]).ok
    bad = tickers_check(["NVDA", "XYZ", "TSM"])
    assert not bad.ok
    assert bad.detail == "not tracked: XYZ; 20-F filers, not ingested: TSM"
