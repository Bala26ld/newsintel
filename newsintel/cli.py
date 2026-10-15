"""newsintel command line."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import typer

from newsintel.core.config import settings

app = typer.Typer(help="Market-moving news intelligence platform", no_args_is_help=True)


def _log():
    logging.basicConfig(level=settings().log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@app.command()
def init_db(drop: bool = typer.Option(False, help="Drop and recreate all tables")):
    """Create tables and seed the source/entity registry from config."""
    from newsintel.store.db import init_db as _init

    _init(drop=drop)
    typer.echo(f"database ready at {settings().database_url}")


@app.command()
def replay(run_id: str = typer.Option(..., help="Run id; rows are scoped to it"), source: str | None = None, fixtures: Path | None = None, pace: bool = False):
    """Feed recorded fixtures through ingest and the pipeline with a frozen clock."""
    _log()
    from newsintel.replay.runner import replay as _replay

    summary = asyncio.run(_replay(run_id, fixture_root=fixtures, source_id=source, pace=pace))
    typer.echo(json.dumps(summary.as_dict(), indent=1, default=str))


@app.command()
def diff(run_a: str, run_b: str, fail_on_diff: bool = typer.Option(True, help="Exit 1 when any difference is found")):
    """Compare two replay runs; a difference in published claims fails."""
    from newsintel.replay.differ import diff_runs, render

    d = diff_runs(run_a, run_b)
    typer.echo(render(d))
    if fail_on_diff and not d.clean:
        raise typer.Exit(code=1)


@app.command()
def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    with_pipeline: bool = typer.Option(False, help="Run the pipeline worker in-process (memory bus)"),
    poll: bool = typer.Option(False, help="Also poll enabled live sources"),
    replay_run: str | None = typer.Option(None, help="On start, replay fixtures into this run id and serve it"),
):
    """Serve the API and the feed page."""
    _log()
    import uvicorn

    from newsintel.api.app import create_app
    from newsintel.bus.base import make_bus

    bus = make_bus(settings().bus, settings().redis_url)

    async def _background(api):
        stop = asyncio.Event()
        tasks = []
        if with_pipeline or poll:
            from newsintel.pipeline.worker import PipelineWorker

            tasks.append(asyncio.create_task(PipelineWorker(bus).run(stop)))
        if poll:
            from newsintel.ingest.scheduler import run_all

            tasks.append(asyncio.create_task(run_all(bus, stop)))
        if replay_run:
            from newsintel.replay.runner import replay as _replay

            await _replay(replay_run, bus=bus if settings().bus == "memory" else None)
            typer.echo(f"replayed fixtures into run {replay_run}; open http://{host}:{port}/?run={replay_run}")
        api.state.background = (stop, tasks)

    async def _teardown(api):
        stop, tasks = api.state.background
        stop.set()
        for t in tasks:
            t.cancel()

    api = create_app(bus, on_startup=_background, on_shutdown=_teardown)
    uvicorn.run(api, host=host, port=port, log_level="info")


@app.command()
def poll():
    """Poll enabled live sources and run the pipeline (no API)."""
    _log()
    from newsintel.bus.base import make_bus
    from newsintel.ingest.scheduler import run_all
    from newsintel.pipeline.worker import PipelineWorker
    from newsintel.store.db import init_db as _init

    async def _main():
        _init()
        bus = make_bus(settings().bus, settings().redis_url)
        stop = asyncio.Event()
        await asyncio.gather(PipelineWorker(bus).run(stop), run_all(bus, stop))

    asyncio.run(_main())


@app.command()
def benchmark():
    """Score extraction candidates (Tier 0 and configured model slots) on the golden set."""
    from newsintel.ai.benchmark import run

    typer.echo(json.dumps(run(), indent=1))


@app.command()
def verify_universe(path: Path = Path("config/universe/us.yaml")):
    """Check each configured CIK against EDGAR's company lookup before going live."""
    import yaml

    from newsintel.adapters.http import PoliteClient

    data = yaml.safe_load(path.read_text())
    client = PoliteClient("newsintel/0.1 (verify-universe; set NEWSINTEL_CONTACT_EMAIL)", 5, ["data.sec.gov", "www.sec.gov"])
    for e in data["entities"]:
        try:
            r = client.get(f"https://data.sec.gov/submissions/CIK{str(e['cik']).zfill(10)}.json").json()
            ok = r.get("name", "").lower().split()[0] == e["name"].lower().split()[0]
            typer.echo(f"{'OK ' if ok else 'CHECK'} {e['cik']} {e['name']} -> {r.get('name')} tickers={r.get('tickers')}")
        except Exception as exc:  # noqa: BLE001
            typer.echo(f"FAIL {e['cik']} {e['name']}: {exc}")


@app.command()
def health():
    """Print source and provider health."""
    from newsintel.ai.router import Router
    from newsintel.ingest.scheduler import source_health
    from newsintel.store.db import init_db as _init

    _init()
    typer.echo(json.dumps({"sources": source_health(), "providers": Router().health()}, indent=1))


if __name__ == "__main__":
    app()
