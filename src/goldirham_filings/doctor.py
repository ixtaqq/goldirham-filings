"""Environment checks for `filings doctor`. The verdicts are pure; probing stays in run_checks."""

from dataclasses import dataclass
from typing import Any

import httpx
import psycopg

from goldirham_filings.config import ROOT, Settings
from goldirham_filings.retrieve import embedding_coverage

EXAMPLE_USER_AGENT = "Your Name you@example.com"


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def user_agent_check(user_agent: str) -> Check:
    name = "SEC_USER_AGENT"
    value = user_agent.strip()
    if not value:
        return Check(name, False, "not set; add 'SEC_USER_AGENT=Your Name you@domain' to .env")
    if value == EXAMPLE_USER_AGENT or "example.com" in value:
        return Check(name, False, "still the example value; use your own name and email")
    if "@" not in value:
        return Check(name, False, f"'{value}' has no contact email; SEC requires name and email")
    return Check(name, True, value)


def migration_check(current: set[str], heads: set[str]) -> Check:
    name = "migrations"
    if current == heads:
        return Check(name, True, f"at head {', '.join(sorted(heads))}")
    found = ", ".join(sorted(current)) or "none applied"
    return Check(
        name,
        False,
        f"{found}; head is {', '.join(sorted(heads))}. "
        r"Run .venv\Scripts\alembic upgrade head",
    )


def model_pulled(model: str, pulled: list[str]) -> bool:
    """Ollama lists 'nomic-embed-text' as 'nomic-embed-text:latest'."""
    wanted = model if ":" in model else f"{model}:latest"
    return wanted in pulled


def model_check(role: str, model: str, pulled: list[str]) -> Check:
    name = f"{role} model"
    if model_pulled(model, pulled):
        return Check(name, True, f"{model} pulled")
    return Check(name, False, f"{model} not pulled; run: ollama pull {model}")


def embedding_check(model: str, embedded: int, chunks: int) -> Check:
    name = "embeddings"
    if chunks == 0:
        return Check(name, False, r"no chunks indexed; run .venv\Scripts\filings ingest")
    share = embedded / chunks
    detail = f"{embedded}/{chunks} chunks ({share:.1%}) embedded with {model}"
    if embedded < chunks:
        return Check(name, False, detail + r"; run .venv\Scripts\filings embed")
    return Check(name, True, detail)


def format_report(checks: list[Check]) -> str:
    return "\n".join(f"{'PASS' if c.ok else 'FAIL'}  {c.name}: {c.detail}" for c in checks)


def _migration_heads() -> set[str]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    return set(ScriptDirectory.from_config(config).get_heads())


def _database_checks(cfg: Settings) -> list[Check]:
    try:
        conn: psycopg.Connection[tuple[Any, ...]] = psycopg.connect(
            cfg.database_url, connect_timeout=5
        )
    except psycopg.Error as e:
        reason = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
        skipped = "not checked: database unreachable"
        return [
            Check(
                "database",
                False,
                f"{reason}. Start Docker Desktop, then run docker compose up -d --wait",
            ),
            *(Check(n, False, skipped) for n in ("migrations", "pgvector", "embeddings")),
        ]
    with conn:
        checks = [Check("database", True, "reachable")]
        has_version_table = conn.execute("SELECT to_regclass('alembic_version')").fetchone()
        current = (
            {str(r[0]) for r in conn.execute("SELECT version_num FROM alembic_version")}
            if has_version_table and has_version_table[0]
            else set()
        )
        checks.append(migration_check(current, _migration_heads()))
        vector = conn.execute(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
        ).fetchone()
        checks.append(
            Check("pgvector", True, f"version {vector[0]}")
            if vector
            else Check("pgvector", False, "extension missing; run migrations on a pgvector image")
        )
        if not current:
            checks.append(Check("embeddings", False, "not checked: schema not migrated"))
            return checks
        embedded, chunks = embedding_coverage(conn, cfg.embedding_model)
        checks.append(embedding_check(cfg.embedding_model, embedded, chunks))
    return checks


def _ollama_checks(cfg: Settings) -> list[Check]:
    try:
        response = httpx.get(f"{cfg.ollama_url}/api/tags", timeout=5)
        response.raise_for_status()
    except httpx.HTTPError as e:
        skipped = "not checked: Ollama unreachable"
        return [
            Check(
                "ollama",
                False,
                f"not reachable at {cfg.ollama_url} ({type(e).__name__}). "
                "Install it from ollama.com and start it",
            ),
            Check("answer model", False, skipped),
            Check("embedding model", False, skipped),
        ]
    pulled = [str(m["name"]) for m in response.json().get("models", [])]
    return [
        Check("ollama", True, f"reachable at {cfg.ollama_url}"),
        model_check("answer", cfg.answer_model, pulled),
        model_check("embedding", cfg.embedding_model, pulled),
    ]


def run_checks(cfg: Settings) -> list[Check]:
    return [*_database_checks(cfg), *_ollama_checks(cfg), user_agent_check(cfg.sec_user_agent)]
