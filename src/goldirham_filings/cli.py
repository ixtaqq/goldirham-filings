import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path, PurePath
from typing import Any

from goldirham_filings.companies import BY_TICKER, TRACKED_COMPANIES
from goldirham_filings.config import ROOT, Settings, settings
from goldirham_filings.db import connect
from goldirham_filings.edgar import EdgarClient, Form
from goldirham_filings.embed import EMBEDDING_MODELS, OllamaEmbedder, embed_missing
from goldirham_filings.errors import DependencyError, database_errors
from goldirham_filings.golden import (
    GoldItem,
    load_golden,
    make_split,
    resolve_offsets,
    save_golden,
    select,
    validate,
)
from goldirham_filings.ingest import IngestReport, ingest, update
from goldirham_filings.retrieve import Filters, Retriever, latest_filings

GOLDEN_PATH = ROOT / "evals" / "golden.jsonl"
BASELINE_PATH = ROOT / "evals" / "baseline.json"
SPLIT_PATH = ROOT / "evals" / "split.json"
SPLIT_SEED = 20261008
BENCHMARKS = ROOT / "benchmarks"
# Latest filings per form for the CI corpus; Microsoft's two 10-Ks let compare_filings and
# the 10-K-to-10-K comparison question run in CI.
FIXTURE_FILINGS: dict[str, dict[Form, int]] = {
    "NVDA": {"10-K": 1, "10-Q": 1},
    "MSFT": {"10-K": 2, "10-Q": 1},
    "MU": {"10-K": 1, "10-Q": 1},
    "AMD": {"10-K": 1, "10-Q": 1},
    "AMAT": {"10-K": 1, "10-Q": 1},
}


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, default=str))


def _write_report(name: str, report: dict[str, Any], split: str = "", label: str = "") -> Path:
    BENCHMARKS.mkdir(exist_ok=True)
    parts = [name, split, date.today().isoformat(), label]
    path = BENCHMARKS / ("-".join(p for p in parts if p) + ".json")
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return path


def cmd_ingest(args: argparse.Namespace, cfg: Settings) -> None:
    tickers = [t.upper() for t in args.tickers] if args.tickers else cfg.ingest_ticker_list
    unknown = sorted(set(tickers) - set(BY_TICKER))
    if unknown:
        sys.exit(f"unknown tickers: {', '.join(unknown)}")
    client = EdgarClient(cfg.sec_user_agent, cfg.sec_requests_per_second)
    with connect() as conn:
        report = ingest(conn, client, cfg.data_dir, [BY_TICKER[t] for t in tickers],
                        {"10-K": args.ten_k, "10-Q": args.ten_q})  # fmt: skip
    _exit_on_failures(report)


def _exit_on_failures(report: IngestReport) -> None:
    print(report.summary())
    if report.failures:
        for f in report.failures:
            print(f"  {f.ticker} {f.accession or '(filing list)'}: {f.error}")
        sys.exit(f"{len(report.failures)} filings or filing lists failed; rerun to retry them")


def cmd_update(args: argparse.Namespace, cfg: Settings) -> None:
    """Index filings newer than the newest one indexed per company, then embed new chunks."""
    unknown = sorted(set(args.tickers or []) - set(BY_TICKER))
    if unknown:
        sys.exit(f"unknown tickers: {', '.join(unknown)}")
    client = EdgarClient(cfg.sec_user_agent, cfg.sec_requests_per_second)
    with connect() as conn:
        if args.tickers:
            companies = [BY_TICKER[t] for t in args.tickers]
        else:
            indexed = {r[0] for r in conn.execute("SELECT DISTINCT cik FROM filings").fetchall()}
            companies = [c for c in TRACKED_COMPANIES if c.cik_int in indexed]
        report = update(conn, client, cfg.data_dir, companies)
        embedder = OllamaEmbedder(cfg.embedding_model, cfg.ollama_url)
        print(f"embedded {embed_missing(conn, embedder)} new chunks with {embedder.model}")
        _load_facts(conn, client, companies)
    _exit_on_failures(report)


def _load_facts(conn: Any, client: EdgarClient, companies: list[Any]) -> None:
    from goldirham_filings.xbrl import fetch_company_facts, parse_company_facts, store_facts

    for company in companies:
        if company.filer_type != "domestic":
            continue
        facts = parse_company_facts(company.cik_int, fetch_company_facts(client, company.cik_int))
        print(f"facts    {company.ticker:5} {store_facts(conn, company.cik_int, facts)}")


def cmd_facts(args: argparse.Namespace, cfg: Settings) -> None:
    """Load XBRL company facts for the given or already-indexed companies."""
    unknown = sorted(set(args.tickers or []) - set(BY_TICKER))
    if unknown:
        sys.exit(f"unknown tickers: {', '.join(unknown)}")
    client = EdgarClient(cfg.sec_user_agent, cfg.sec_requests_per_second)
    with connect() as conn:
        if args.tickers:
            companies = [BY_TICKER[t] for t in args.tickers]
        else:
            indexed = {r[0] for r in conn.execute("SELECT DISTINCT cik FROM filings").fetchall()}
            companies = [c for c in TRACKED_COMPANIES if c.cik_int in indexed]
        _load_facts(conn, client, companies)


def cmd_embed(args: argparse.Namespace, cfg: Settings) -> None:
    embedder = OllamaEmbedder(args.model or cfg.embedding_model, cfg.ollama_url)
    with connect() as conn:
        print(f"embedded {embed_missing(conn, embedder)} chunks with {embedder.model}")


def cmd_search(args: argparse.Namespace, cfg: Settings) -> None:
    with connect() as conn:
        retriever = Retriever(conn, OllamaEmbedder(cfg.embedding_model, cfg.ollama_url))
        passages = retriever.search(args.query, Filters(tickers=args.tickers), k=args.k)
    _print_json([p.model_dump(mode="json") for p in passages])


def cmd_answer(args: argparse.Namespace, cfg: Settings) -> None:
    from goldirham_filings.answer import Answerer, OllamaChat

    with connect() as conn:
        retriever = Retriever(conn, OllamaEmbedder(cfg.embedding_model, cfg.ollama_url))
        answerer = Answerer(retriever, OllamaChat(cfg.answer_model, cfg.ollama_url),
                            min_similarity=cfg.abstain_min_similarity)  # fmt: skip
        result = answerer.answer(args.question, Filters(tickers=args.tickers))
    _print_json(result.model_dump(mode="json"))


def cmd_golden(args: argparse.Namespace, cfg: Settings) -> None:
    items = load_golden(args.path)
    problems: list[str] = []
    with connect() as conn:
        if args.action == "resolve":
            items, problems = resolve_offsets(conn, items)
            save_golden(args.path, items)
        problems += validate(conn, items)
    print("\n".join(problems) or f"{len(items)} items valid")
    if problems:
        sys.exit(1)


def _split_items(args: argparse.Namespace) -> list[GoldItem]:
    """Golden items of the requested split. Tuning uses dev; test is for reported numbers."""
    items = load_golden(args.golden)
    if args.split == "all":
        return items
    return select(items, json.loads(SPLIT_PATH.read_text(encoding="utf-8")), args.split)


def cmd_golden_split(args: argparse.Namespace, cfg: Settings) -> None:
    split = make_split(load_golden(args.golden), SPLIT_SEED, test_fraction=1 / 3)
    if SPLIT_PATH.exists() and json.loads(SPLIT_PATH.read_text(encoding="utf-8")) != split:
        sys.exit(f"{SPLIT_PATH.name} exists and differs; the split is fixed once tuning starts")
    SPLIT_PATH.write_text(json.dumps(split, indent=2) + "\n", encoding="utf-8")
    print(f"dev {len(split['dev'])}, test {len(split['test'])} -> {SPLIT_PATH}")


def cmd_eval_retrieval(args: argparse.Namespace, cfg: Settings) -> None:
    """Compare embedding models (and modes) on the golden set's retrieval metrics."""
    from goldirham_filings.evaluate import rows, run_retrieval, summarize_retrieval

    selected = _split_items(args)
    items = [i for i in selected if i.answerable]
    unanswerable = [i for i in selected if not i.answerable]
    report: dict[str, Any] = {"golden": str(args.golden), "split": args.split,
                              "label": args.label, "runs": []}  # fmt: skip
    with connect() as conn:
        for model in args.models or [cfg.embedding_model]:
            for mode in args.modes:
                retriever = Retriever(conn, OllamaEmbedder(model, cfg.ollama_url), mode=mode)
                scores = run_retrieval(retriever, items)
                summary = summarize_retrieval(scores)
                negatives = run_retrieval(retriever, unanswerable) if mode != "keyword" else []
                report["runs"].append({
                    "model": model, "mode": mode, "summary": summary, "items": rows(scores),
                    "unanswerable_top_similarity": [s.top_similarity for s in negatives],
                })  # fmt: skip
                print(f"{model:20} {mode:8} {summary}")
    print(f"wrote {_write_report('retrieval', report, args.split, args.label)}")


def cmd_eval_full(args: argparse.Namespace, cfg: Settings) -> None:
    from goldirham_filings.answer import Answerer, OllamaChat
    from goldirham_filings.evaluate import (
        rows,
        run_answers,
        run_retrieval,
        similarity_threshold,
        summarize_answers,
        summarize_retrieval,
    )

    items = _split_items(args)
    chat = OllamaChat(cfg.answer_model, cfg.ollama_url)
    with connect() as conn:
        retriever = Retriever(conn, OllamaEmbedder(cfg.embedding_model, cfg.ollama_url))
        answerer = Answerer(retriever, chat, min_similarity=cfg.abstain_min_similarity)
        retrieval = run_retrieval(retriever, [i for i in items if i.answerable])
        # The abstention signal is the best similarity among the passages the answerer sees.
        positives = run_retrieval(retriever, [i for i in items if i.answerable], k=answerer.k)
        negatives = run_retrieval(retriever, [i for i in items if not i.answerable], answerer.k)
        suggested = similarity_threshold(
            [s.top_similarity or 0.0 for s in positives],
            [s.top_similarity or 0.0 for s in negatives],
        )
        answers = run_answers(answerer, chat, items)
    report = {
        "config": {"embedding_model": cfg.embedding_model, "answer_model": cfg.answer_model,
                   "abstain_min_similarity": cfg.abstain_min_similarity, "k": answerer.k,
                   "drop_unsupported_numbers": answerer.drop_unsupported_numbers},
        "golden": {"path": str(args.golden), "split": args.split, "label": args.label,
                   "items": len(items),
                   "human_checked": sum(i.checked_by is not None for i in items)},
        "retrieval": summarize_retrieval(retrieval),
        "suggested_abstain_threshold": asdict(suggested),
        "answers": summarize_answers(answers),
        "retrieval_items": rows(retrieval),
        "answer_items": rows(answers),
    }  # fmt: skip
    _print_json({k: report[k] for k in ("config", "golden", "retrieval", "answers")})
    print(f"wrote {_write_report('eval', report, args.split, args.label)}")


def _gate_items(items: list[GoldItem], accessions: list[str]) -> list[GoldItem]:
    inside = set(accessions)
    return [
        i for i in items
        if i.answerable and all(s.accession in inside for s in i.gold_spans)
    ]  # fmt: skip


def cmd_eval_gate(args: argparse.Namespace, cfg: Settings) -> None:
    """Retrieval gate over the fixture corpus, using precomputed question vectors."""
    from goldirham_filings.evaluate import gate, run_retrieval, summarize_retrieval
    from goldirham_filings.fixtures import FixtureEmbedder

    embedder = FixtureEmbedder(args.fixtures)
    items = _gate_items(load_golden(args.golden), embedder.accessions)
    with connect() as conn:
        summary = summarize_retrieval(
            run_retrieval(Retriever(conn, embedder, mode=args.mode), items)
        )
    print(f"mode={args.mode} {summary}")
    measured = {
        "embedding_model": embedder.model,
        "mode": args.mode,
        "scope_from_query": True,
        "item_ids": [i.id for i in items],
        "human_checked": sum(i.checked_by is not None for i in items),
        **summary,
    }
    if args.write_baseline:
        BASELINE_PATH.write_text(json.dumps(measured, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {BASELINE_PATH}")
        return
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    keys = ("embedding_model", "mode", "scope_from_query", "item_ids")
    mismatched = [k for k in keys if baseline.get(k) != measured[k]]
    if mismatched:
        sys.exit(f"baseline was recorded with different {', '.join(mismatched)}; "
                 "rerun with --write-baseline as a reviewed change")  # fmt: skip
    failures = gate(summary, baseline)
    print("\n".join(failures) or f"gate passed against {BASELINE_PATH.name}")
    if failures:
        sys.exit(1)


def cmd_fixtures(args: argparse.Namespace, cfg: Settings) -> None:
    from goldirham_filings import fixtures

    with connect() as conn:
        if args.action == "load":
            print(f"loaded {fixtures.load(conn, args.fixtures)} chunks")
            return
        model = args.model or cfg.embedding_model
        accessions = [
            f.accession
            for ticker, forms in FIXTURE_FILINGS.items()
            for form, count in forms.items()
            for f in latest_filings(conn, ticker, form, limit=count)
        ]
        embedder = OllamaEmbedder(model, cfg.ollama_url)
        items = _gate_items(load_golden(args.golden), accessions)
        vectors = {i.question: embedder.embed_query(i.question) for i in items}
        manifest = fixtures.export(conn, accessions, model, vectors, args.fixtures)
    print(f"exported {manifest['chunks']} chunks, {len(vectors)} question vectors")


def mcp_config(server_command: PurePath) -> dict[str, Any]:
    """Claude Desktop's mcpServers block for this install's stdio server."""
    return {"mcpServers": {"goldirham-filings": {"command": str(server_command)}}}


def server_command() -> Path:
    """The console script installed next to this interpreter in the project's venv."""
    name = "goldirham-filings-mcp.exe" if os.name == "nt" else "goldirham-filings-mcp"
    return Path(sys.executable).parent / name


def cmd_mcp_config(args: argparse.Namespace, cfg: Settings) -> None:
    command = server_command()
    if not command.exists():
        sys.exit(f"{command} not found; run python -m uv sync in {ROOT}")
    print(json.dumps(mcp_config(command), indent=2))
    where = (r"%APPDATA%\Claude\claude_desktop_config.json" if os.name == "nt"
             else "~/Library/Application Support/Claude/claude_desktop_config.json")  # fmt: skip
    print(f"Merge this into {where}, then restart Claude Desktop.", file=sys.stderr)


def cmd_doctor(args: argparse.Namespace, cfg: Settings) -> None:
    from goldirham_filings.doctor import format_report, run_checks

    checks = run_checks(cfg)
    print(format_report(checks))
    failed = sum(not c.ok for c in checks)
    if failed:
        sys.exit(f"{failed} of {len(checks)} checks failed")
    print(f"all {len(checks)} checks passed")


def main(argv: list[str] | None = None) -> None:
    from goldirham_filings.fixtures import FIXTURE_DIR

    parser = argparse.ArgumentParser(prog="filings")
    commands = parser.add_subparsers(dest="command", required=True)

    p = commands.add_parser("ingest", help="fetch, parse and chunk filings")
    p.add_argument("--tickers", nargs="+", help="default: INGEST_TICKERS from .env")
    p.add_argument("--10k", dest="ten_k", type=int, default=2)
    p.add_argument("--10q", dest="ten_q", type=int, default=6)
    p.set_defaults(func=cmd_ingest)

    p = commands.add_parser("update", help="index filings newer than the index, then embed them")
    p.add_argument("--tickers", nargs="+", help="default: every company already indexed")
    p.set_defaults(func=cmd_update)

    p = commands.add_parser("facts", help="load XBRL company facts from EDGAR")
    p.add_argument("--tickers", nargs="+", help="default: every company already indexed")
    p.set_defaults(func=cmd_facts)

    p = commands.add_parser("embed", help="embed chunks that have no vector yet")
    p.add_argument("--model", choices=sorted(EMBEDDING_MODELS))
    p.set_defaults(func=cmd_embed)

    p = commands.add_parser("search", help="hybrid search, printed as JSON")
    p.add_argument("query")
    p.add_argument("--tickers", nargs="+")
    p.add_argument("-k", type=int, default=8)
    p.set_defaults(func=cmd_search)

    p = commands.add_parser("answer", help="cited answer, printed as JSON")
    p.add_argument("question")
    p.add_argument("--tickers", nargs="+")
    p.set_defaults(func=cmd_answer)

    p = commands.add_parser("split", help="write the fixed dev/test split of the golden set")
    p.add_argument("--golden", type=Path, default=GOLDEN_PATH)
    p.set_defaults(func=cmd_golden_split)

    p = commands.add_parser("golden", help="resolve or validate golden-set offsets")
    p.add_argument("action", choices=["resolve", "validate"])
    p.add_argument("path", type=Path, nargs="?", default=GOLDEN_PATH)
    p.set_defaults(func=cmd_golden)

    p = commands.add_parser("eval", help="retrieval comparison, full eval, or CI gate")
    evals = p.add_subparsers(dest="eval_command", required=True)
    e = evals.add_parser("retrieval")
    e.add_argument("--models", nargs="+", choices=sorted(EMBEDDING_MODELS))
    e.add_argument("--modes", nargs="+", default=["hybrid"],
                   choices=["hybrid", "keyword", "vector"])  # fmt: skip
    e.set_defaults(func=cmd_eval_retrieval)
    e = evals.add_parser("full")
    e.set_defaults(func=cmd_eval_full)
    e = evals.add_parser("gate")
    e.add_argument("--mode", default="hybrid", choices=["hybrid", "keyword", "vector"])
    e.add_argument("--fixtures", type=Path, default=FIXTURE_DIR)
    e.add_argument("--write-baseline", action="store_true")
    e.set_defaults(func=cmd_eval_gate)
    for e in (evals.choices["retrieval"], evals.choices["full"], evals.choices["gate"]):
        e.add_argument("--golden", type=Path, default=GOLDEN_PATH)
    for e in (evals.choices["retrieval"], evals.choices["full"]):
        e.add_argument("--split", default="dev", choices=["dev", "test", "all"],
                       help="dev for tuning; test only for reported milestones")  # fmt: skip
        e.add_argument("--label", default="", help="appended to the report file name")

    p = commands.add_parser("fixtures", help="export or load the CI fixture corpus")
    p.add_argument("action", choices=["export", "load"])
    p.add_argument("--fixtures", type=Path, default=FIXTURE_DIR)
    p.add_argument("--model", choices=sorted(EMBEDDING_MODELS))
    p.add_argument("--golden", type=Path, default=GOLDEN_PATH)
    p.set_defaults(func=cmd_fixtures)

    p = commands.add_parser("mcp-config", help="print the Claude Desktop config for this install")
    p.set_defaults(func=cmd_mcp_config)

    p = commands.add_parser("doctor", help="check the database, Ollama, models and settings")
    p.set_defaults(func=cmd_doctor)

    args = parser.parse_args(argv)
    cfg = settings()
    try:
        with database_errors(cfg.database_url):
            args.func(args, cfg)
    except DependencyError as error:
        sys.exit(f"error: {error}")


if __name__ == "__main__":
    main()
