# newsintel

Evidence-first market-moving news intelligence for India, the UK and the US. The platform
detects official announcements (exchange filings, regulators, central banks, company IR),
extracts structured claims with verbatim evidence spans, verifies them, ranks materiality,
and publishes source-linked alerts. It never produces buy/sell recommendations.

Phase 1 (this repository state) is the end-to-end vertical slice on recorded fixtures:
SEC EDGAR adapter → ingest → extract (Tier 0 rules) → resolve → dedupe → verify → context →
rank → gate → publish → SSE feed, with a replay harness whose run-to-run diff is the CI gate.

## Quick start (no services needed)

```bash
pip install -e ".[dev]"
newsintel replay --run-id demo            # replay the recorded fixtures through the pipeline
newsintel serve --replay-run demo         # then open http://127.0.0.1:8000/?run=demo
```

The feed page asks for an API key; the development keys are `dev-reader-key` and
`dev-reviewer-key` (set `NEWSINTEL_API_KEYS_*` in `.env` for anything else).

Replay twice and diff — any difference in events, claims, alerts or decisions is a failure:

```bash
newsintel replay --run-id a && newsintel replay --run-id b && newsintel diff a b
```

Run the tests and the extraction benchmark:

```bash
pytest -q
newsintel benchmark        # Tier 0 always; model slots only when their API keys are set
```

## Live polling

1. Copy `.env.example` to `.env`; set `NEWSINTEL_CONTACT_EMAIL` (EDGAR's fair-access policy asks for a contact in the User-Agent).
2. Confirm each source's current terms at the `terms_url` in `config/sources.yaml` before enabling it.
3. `newsintel verify-universe` checks the configured CIKs against EDGAR's company lookup.
4. `newsintel serve --with-pipeline --poll` (single process, memory bus) or `docker compose up`
   (Postgres + Redis Streams, API and poller as separate processes).

Every live fetch is recorded under `fixtures/<source>/<date>/` in the same JSON shape as the
synthetic fixtures, so anything seen live can be replayed and added to the golden set.

## Layout

```
config/            sources.yaml (registry + terms), policy.yaml (gate thresholds, weights), providers.yaml, markets.yaml, universe/
newsintel/core     schemas, money/date parsing, fingerprints, deterministic ids, frozen clock
newsintel/adapters base protocol, EDGAR adapter, polite HTTP client, fixture recorder
newsintel/ingest   snapshot store, ingest service, polling scheduler
newsintel/bus      memory bus (single process) and Redis Streams bus (multi-process)
newsintel/pipeline extract/ resolve/ dedupe/ verify/ context/ rank/ gate/ publish/ worker.py review.py
newsintel/ai       provider protocol, router (slots, budgets, fallbacks, audit), vendor adapters, benchmark
newsintel/api      FastAPI app, SSE stream, review endpoints, static feed page
newsintel/replay   fixture replay runner and run differ
fixtures/          recorded snapshots (synthetic in Phase 1) and golden/labels.yaml
tools/             make_synthetic_fixtures.py
```

## Design

The full design and the phase plan live in the "News Intelligence Platform — Design &
Implementation Plan" doc. Two rules from it are enforced in code and tested:

- every sentence in a published alert cites at least one supported claim, and every claim's
  span reproduces verbatim from the stored document (`tests/test_pipeline_replay.py`);
- the gate publishes only verified official events; unsupported claims, unresolved entities,
  duplicates and amounts above 25% of last annualised revenue are held
  (`newsintel/pipeline/gate/policy.py`, `config/policy.yaml`).

The fixture companies (Halvard Dynamics, Meridian Foods, ...) are fictional. Real filings
could not be fetched from the build environment; the adapter targets EDGAR's real document
layout and records real fixtures on its first live run.
