import argparse
import json
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from goldirham_filings.companies import BY_TICKER
from goldirham_filings.config import ROOT, Settings, settings
from goldirham_filings.db import connect
from goldirham_filings.edgar import EdgarClient, Form
from goldirham_filings.embed import EMBEDDING_MODELS, OllamaEmbedder, embed_missing
from goldirham_filings.golden import GoldItem, load_golden, resolve_offsets, save_golden, validate
from goldirham_filings.ingest import M1_TICKERS, ingest
from goldirham_filings.retrieve import Filters, Retriever, latest_filings

GOLDEN_PATH = ROOT / "evals" / "golden.jsonl"
BASELINE_PATH = ROOT / "evals" / "baseline.json"
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


def _write_report(name: str, report: dict[str, Any]) -> Path:
    BENCHMARKS.mkdir(exist_ok=True)
    path = BENCHMARKS / f"{name}-{date.today().isoformat()}.json"
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return path


def cmd_ingest(args: argparse.Namespace, cfg: Settings) -> None:
    unknown = sorted(set(args.tickers) - set(BY_TICKER))
    if unknown:
        sys.exit(f"unknown tickers: {', '.join(unknown)}")
    client = EdgarClient(cfg.sec_user_agent, cfg.sec_requests_per_second)
    with connect() as conn:
        results = ingest(conn, client, cfg.data_dir, [BY_TICKER[t] for t in args.tickers],
                         {"10-K": args.ten_k, "10-Q": args.ten_q})  # fmt: skip
    missing = [r for r in results if r.missing]
    print(f"{len(results)} filings; {len(missing)} with missing required sections")


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


def cmd_eval_retrieval(args: argparse.Namespace, cfg: Settings) -> None:
    """Compare embedding models (and modes) on the golden set's retrieval metrics."""
    from goldirham_filings.evaluate import rows, run_retrieval, summarize_retrieval

    items = [i for i in load_golden(args.golden) if i.answerable]
    unanswerable = [i for i in load_golden(args.golden) if not i.answerable]
    report: dict[str, Any] = {"golden": str(args.golden), "runs": []}
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
    print(f"wrote {_write_report('retrieval', report)}")


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

    items = load_golden(args.golden)
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
                   "abstain_min_similarity": cfg.abstain_min_similarity, "k": answerer.k},
        "golden": {"path": str(args.golden), "items": len(items),
                   "human_checked": sum(i.checked_by is not None for i in items)},
        "retrieval": summarize_retrieval(retrieval),
        "suggested_abstain_threshold": asdict(suggested),
        "answers": summarize_answers(answers),
        "retrieval_items": rows(retrieval),
        "answer_items": rows(answers),
    }  # fmt: skip
    _print_json({k: report[k] for k in ("config", "golden", "retrieval", "answers")})
    print(f"wrote {_write_report('eval', report)}")


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


def main(argv: list[str] | None = None) -> None:
    from goldirham_filings.fixtures import FIXTURE_DIR

    parser = argparse.ArgumentParser(prog="filings")
    commands = parser.add_subparsers(dest="command", required=True)

    p = commands.add_parser("ingest", help="fetch, parse and chunk filings")
    p.add_argument("--tickers", nargs="+", default=list(M1_TICKERS))
    p.add_argument("--10k", dest="ten_k", type=int, default=2)
    p.add_argument("--10q", dest="ten_q", type=int, default=6)
    p.set_defaults(func=cmd_ingest)

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

    p = commands.add_parser("fixtures", help="export or load the CI fixture corpus")
    p.add_argument("action", choices=["export", "load"])
    p.add_argument("--fixtures", type=Path, default=FIXTURE_DIR)
    p.add_argument("--model", choices=sorted(EMBEDDING_MODELS))
    p.add_argument("--golden", type=Path, default=GOLDEN_PATH)
    p.set_defaults(func=cmd_fixtures)

    args = parser.parse_args(argv)
    args.func(args, settings())


if __name__ == "__main__":
    main()
