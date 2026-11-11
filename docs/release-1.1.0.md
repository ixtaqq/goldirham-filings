# Release 1.1.0

Prepared, not shipped. Nothing here has been tagged or published.

## Version

Minor, from the diff since `v1.0.0`: new settings (`DROP_UNSUPPORTED_PERIODS`,
`CHECK_FACT_DURATIONS`, `INGEST_FOREIGN_FILERS`), `20-F` added to the tool schemas' form enum,
and migration 0005. One tightening a client could notice: tool inputs now have length limits,
and `compare_filings` refuses a single accession. The longest golden question is 254
characters against a limit of 2000, and the single-accession call returned the wrong filings,
so this ships as a minor.

## Before tagging

1. Merge the stack in order: #4, #5, #6, #7, #8, #9, then the PRs for
   `feat/v1.1-answer-quality`, `feat/20-f-filers` and `chore/readiness`. Delete each branch on
   merge so the next PR retargets to `main`.
2. Confirm CI is green on `main`.
3. Change the `CHANGELOG.md` heading's date to the release date.

## Ship

```
git checkout main
git pull
git tag v1.1.0
git push origin v1.1.0
```

## Upgrade an install

```
git pull
python -m uv sync
.venv\Scripts\alembic upgrade head
.venv\Scripts\filings doctor
```

Migration 0005 swaps a CHECK constraint on `filings` and `xbrl_facts`. It took about a second
on the fixture database and sets a 5-second lock timeout. Rerun it if the timeout fires.

## Roll back

1. If any 20-F filing is indexed, remove it first. The downgrade fails while one exists.
2. `.venv\Scripts\alembic downgrade -1`
3. `git checkout v1.0.0`, then `python -m uv sync`.

Nothing in this release is irreversible. The tag can be deleted until someone depends on it.

## Before turning on the off-by-default settings

| Setting | Needs first |
| --- | --- |
| `DROP_UNSUPPORTED_PERIODS` | Unanswerable accuracy and false declines on the dev index |
| `CHECK_FACT_DURATIONS` | A dev eval with real XBRL facts loaded |
| `INGEST_FOREIGN_FILERS` | One real 20-F parsed and read against its filing |

The dev index needs `SEC_USER_AGENT` in `.env`, `ollama pull nomic-embed-text`, and
`filings ingest`, `filings embed`, `filings facts`. `scripts\mcp_smoke.py` and
`scripts\eval.ps1` run against it.
