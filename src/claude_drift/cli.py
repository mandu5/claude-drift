from __future__ import annotations

import shutil
import threading
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime

import click

from claude_drift import __version__
from claude_drift.ingest import ingest, projects_root
from claude_drift.models import CutPoint, ReplayResult
from claude_drift.replay import (
    Runner,
    SubprocessRunner,
    blocking_settings_path,
    claude_version,
    replay_cut,
)
from claude_drift.report import build_report
from claude_drift.sample import batch_by_session, sample_cuts
from claude_drift.store import Run, get_run, latest_run, new_run


@click.group()
@click.version_option(__version__, prog_name="drift")
def main() -> None:
    """Replay your Claude Code sessions against a new model and see what changed."""


@main.command()
@click.option(
    "--project", type=click.Path(), default=None, help="Only sessions whose cwd is under this path."
)
def scan(project: str | None) -> None:
    """List local sessions, recorded models, and replayable cut counts. Makes no model calls."""
    root = projects_root()
    cuts = ingest(root, project=project)
    sessions = {c.session_id for c in cuts}
    click.echo(f"projects root: {root}")
    click.echo(f"sessions with replayable cuts: {len(sessions)}")
    click.echo(f"replayable cuts: {len(cuts)}")
    if not cuts:
        return
    click.echo("\nby recorded model:")
    for model, n in Counter(c.model for c in cuts).most_common():
        click.echo(f"  {model:<28} {n:>5}")
    click.echo("\nby recorded tool:")
    for tool, n in Counter(c.recorded.tool for c in cuts).most_common():
        click.echo(f"  {tool:<28} {n:>5}")


TOKENS_PER_CUT_ESTIMATE = 100_000
ABORT_ERROR_RATE = 0.20
ABORT_MIN_COMPLETED = 5


def resolve_from_model(cuts: list[CutPoint], from_model: str) -> str:
    names = sorted({c.model for c in cuts})
    exact = [n for n in names if n == from_model]
    if exact:
        return exact[0]
    partial = [n for n in names if from_model in n]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        raise click.ClickException(
            f"--from {from_model!r} matches no recorded model. "
            f"Recorded: {', '.join(names) or 'none'}"
        )
    raise click.ClickException(
        f"--from {from_model!r} matches more than one recorded model: {', '.join(partial)}"
    )


@dataclass
class _Progress:
    completed: int = 0
    errors: int = 0
    aborted: bool = False


def run_replay(
    *,
    from_model: str,
    to_model: str,
    turns: int,
    workers: int,
    noise: bool,
    project: str | None,
    seed: int,
    timeout: float,
    runner: Runner,
    echo: Callable[[str], None],
) -> Run:
    all_cuts = ingest(projects_root(), project=project)
    if not all_cuts:
        raise click.ClickException("no replayable cuts found; run `drift scan`")
    resolved_from = resolve_from_model(all_cuts, from_model)
    cuts = sample_cuts([c for c in all_cuts if c.model == resolved_from], turns=turns, seed=seed)
    if not cuts:
        raise click.ClickException(f"no cuts recorded with model {resolved_from}")
    run = new_run(datetime.now())
    run.write_cuts(cuts)
    manifest = {
        "from": resolved_from,
        "to": to_model,
        "turns": len(cuts),
        "seed": seed,
        "workers": workers,
        "noise": noise,
        "timeout": timeout,
        "claude_version": claude_version(),
        "started": datetime.now().isoformat(),
        "status": "running",
    }
    run.write_manifest(manifest)

    state = _Progress()
    lock = threading.Lock()

    def record(r: ReplayResult) -> None:
        run.append_replay(r)
        with lock:
            state.completed += 1
            if r.error:
                state.errors += 1
            if (
                state.completed >= ABORT_MIN_COMPLETED
                and state.errors / state.completed > ABORT_ERROR_RATE
            ):
                state.aborted = True
        outcome = r.signature.key if r.signature else r.error
        total = len(cuts) * (2 if noise else 1)
        echo(f"[{state.completed}/{total}] {r.cut_id} {r.model} -> {outcome}")

    def work(batch: list[CutPoint]) -> None:
        for model, role in [(to_model, "candidate")] + (
            [(resolved_from, "noise")] if noise else []
        ):
            for cut in batch:
                if state.aborted:
                    return
                record(replay_cut(cut, model, role, runner, timeout=timeout))

    # Write the deny-all settings file once, before any worker can race on it.
    blocking_settings_path()

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(work, batch_by_session(cuts)))
    except Exception as exc:
        manifest.update(
            {
                "finished": datetime.now().isoformat(),
                "completed": state.completed,
                "errors": state.errors,
                "status": "failed",
                "failure": f"{type(exc).__name__}: {exc}",
            }
        )
        run.write_manifest(manifest)
        raise

    manifest.update(
        {
            "finished": datetime.now().isoformat(),
            "completed": state.completed,
            "errors": state.errors,
            "status": "aborted" if state.aborted else "done",
        }
    )
    run.write_manifest(manifest)
    return run


@main.command()
@click.option(
    "--from",
    "from_model",
    required=True,
    help="Recorded model, e.g. opus-5 or claude-opus-5.",
)
@click.option(
    "--to",
    "to_model",
    required=True,
    help="Model to replay with, passed to `claude --model`.",
)
@click.option("--turns", default=60, show_default=True, type=int)
@click.option("--workers", default=4, show_default=True, type=int)
@click.option(
    "--no-noise",
    "no_noise",
    is_flag=True,
    help="Skip the old-model replay that measures the noise band.",
)
@click.option("--project", type=click.Path(), default=None)
@click.option("--seed", default=0, show_default=True, type=int)
@click.option(
    "--timeout",
    default=180.0,
    show_default=True,
    type=float,
    help="Seconds per replay.",
)
@click.option("--yes", "-y", is_flag=True, help="Skip the cost confirmation.")
def replay(
    from_model: str,
    to_model: str,
    turns: int,
    workers: int,
    no_noise: bool,
    project: str | None,
    seed: int,
    timeout: float,
    yes: bool,
) -> None:
    """Replay sampled cuts with the new model (and the old one for the noise band).

    Uses your claude login.
    """
    if shutil.which("claude") is None:
        raise click.ClickException(
            "claude binary not found on PATH; install Claude Code and log in first"
        )
    noise = not no_noise
    all_cuts = ingest(projects_root(), project=project)
    if not all_cuts:
        raise click.ClickException("no replayable cuts found; run `drift scan`")
    resolved = resolve_from_model(all_cuts, from_model)
    n = len(sample_cuts([c for c in all_cuts if c.model == resolved], turns=turns, seed=seed))
    est = n * (2 if noise else 1) * TOKENS_PER_CUT_ESTIMATE
    click.echo(f"cuts: {n}  replays: {n * (2 if noise else 1)}  estimated input tokens: {est:,}")
    if not yes:
        click.confirm("continue?", abort=True)
    run = run_replay(
        from_model=from_model,
        to_model=to_model,
        turns=turns,
        workers=workers,
        noise=noise,
        project=project,
        seed=seed,
        timeout=timeout,
        runner=SubprocessRunner(),
        echo=click.echo,
    )
    m = run.read_manifest()
    click.echo(
        f"status: {m['status']}  completed: {m['completed']}  failed replays: {m['errors']}"
    )
    click.echo(f"run id: {run.path.name}")
    click.echo()
    click.echo(build_report(run), nl=False)
    if m["status"] == "aborted":
        raise click.exceptions.Exit(1)


@main.command()
@click.option(
    "--run",
    "run_id",
    default=None,
    help="Run id (folder name under ~/.claude-drift/runs). Default: latest.",
)
@click.option(
    "--format", "fmt", type=click.Choice(["text", "md"]), default="text", show_default=True
)
def report(run_id: str | None, fmt: str) -> None:
    """Render the drift report from a stored run. Makes no model calls."""
    try:
        run = get_run(run_id) if run_id else latest_run()
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    if run is None:
        raise click.ClickException("no runs yet; run `drift replay` first")
    click.echo(build_report(run, fmt=fmt), nl=False)
