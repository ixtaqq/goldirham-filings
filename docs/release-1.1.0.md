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

| Setting | Dev result | Needs first |
| --- | --- | --- |
| `DROP_UNSUPPORTED_PERIODS` | Never fired on 48 items | A set with period-trap questions |
| `CHECK_FACT_DURATIONS` | Never fired on 48 items | The same |
| `COMPARISON_TOPIC_QUERIES` | Recall@10 0.742 to 0.802; precision 0.662 to 0.657 | Re-exported fixtures and a reviewed baseline: the CI gate has no vectors for topics |
| `INGEST_FOREIGN_FILERS` | Parser read against 3 real filings | Golden questions for those filers |

The new held-out set (`docs/v1.1-plan.md`, item 5) is the place to measure these.
