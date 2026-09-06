from __future__ import annotations

import shutil
import signal
import threading
from collections import Counter, defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Any

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
from claude_drift.sample import sample_cuts
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


def _raise_sigterm(signum: int, frame: object) -> None:
    """SIGTERM handler: turn the signal into a catchable SystemExit(143).

    Python's default SIGTERM handling terminates the process immediately, which skips
    every `finally` block and leaves temp_session copies behind under ~/.claude/projects
    and the run manifest stuck at status "running". Raising SystemExit instead lets
    run_replay's exception handling clean up before the process exits.
    """
    raise SystemExit(143)


# Within one session batch, replay every candidate cut before any noise cut, matching the
# order a single (to_model, "candidate") + (from_model, "noise") pass would produce.
_ROLE_ORDER = {"candidate": 0, "noise": 1}


def _batch_items_by_session(
    items: list[tuple[CutPoint, str, str]],
) -> list[list[tuple[CutPoint, str, str]]]:
    by_session: dict[str, list[tuple[CutPoint, str, str]]] = defaultdict(list)
    for item in items:
        by_session[item[0].session_path].append(item)
    return [
        sorted(g, key=lambda it: (_ROLE_ORDER.get(it[2], 2), it[0].line_index))
        for _, g in sorted(by_session.items())
    ]


def _execute(
    run: Run,
    cuts_by_role: list[tuple[CutPoint, str, str]],
    manifest: dict[str, Any],
    runner: Runner,
    workers: int,
    timeout: float,
    echo: Callable[[str], None],
) -> _Progress:
    """Replay each (cut, model, role) in `cuts_by_role`, one session per worker.

    Appends every result to `run`'s replays.jsonl and updates `manifest` in place with
    the same status/finished/completed/errors bookkeeping `run_replay` and `resume` both need.
    """
    state = _Progress()
    lock = threading.Lock()
    total = len(cuts_by_role)

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
        echo(f"[{state.completed}/{total}] {r.cut_id} {r.model} -> {outcome}")

    def work(batch: list[tuple[CutPoint, str, str]]) -> None:
        for cut, model, role in batch:
            if state.aborted:
                return
            try:
                result = replay_cut(cut, model, role, runner, timeout=timeout)
            except BaseException:
                # Stop other in-flight/queued batches from starting new cuts as soon
                # as possible, before the pool shutdown below waits for them.
                state.aborted = True
                raise
            record(result)

    # Write the deny-all settings file once, before any worker can race on it.
    blocking_settings_path()

    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        list(pool.map(work, _batch_items_by_session(cuts_by_role)))
    except (KeyboardInterrupt, SystemExit) as exc:
        state.aborted = True
        pool.shutdown(wait=True)
        manifest.update(
            {
                "finished": datetime.now().isoformat(),
                "completed": state.completed,
                "errors": state.errors,
                "status": "interrupted",
                "failure": f"{type(exc).__name__}",
            }
        )
        run.write_manifest(manifest)
        raise
    except Exception as exc:
        pool.shutdown(wait=True)
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
    else:
        pool.shutdown(wait=True)

    manifest.update(
        {
            "finished": datetime.now().isoformat(),
            "completed": state.completed,
            "errors": state.errors,
            "status": "aborted" if state.aborted else "done",
        }
    )
    run.write_manifest(manifest)
    return state


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
    per_session: int = 3,
) -> Run:
    all_cuts = ingest(projects_root(), project=project)
    if not all_cuts:
        raise click.ClickException("no replayable cuts found; run `drift scan`")
    resolved_from = resolve_from_model(all_cuts, from_model)
    cuts = sample_cuts(
        [c for c in all_cuts if c.model == resolved_from],
        turns=turns,
        seed=seed,
        per_session=per_session,
    )
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
        "per_session": per_session,
        "claude_version": claude_version(),
        "started": datetime.now().isoformat(),
        "status": "running",
    }
    run.write_manifest(manifest)

    items: list[tuple[CutPoint, str, str]] = [(c, to_model, "candidate") for c in cuts]
    if noise:
        items += [(c, resolved_from, "noise") for c in cuts]

    _execute(run, items, manifest, runner, workers, timeout, echo)
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
@click.option(
    "--per-session",
    "per_session",
    default=3,
    show_default=True,
    type=int,
    help="Max cuts sampled from one session.",
)
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
    per_session: int,
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
    signal.signal(signal.SIGTERM, _raise_sigterm)
    if shutil.which("claude") is None:
        raise click.ClickException(
            "claude binary not found on PATH; install Claude Code and log in first"
        )
    noise = not no_noise
    all_cuts = ingest(projects_root(), project=project)
    if not all_cuts:
        raise click.ClickException("no replayable cuts found; run `drift scan`")
    resolved = resolve_from_model(all_cuts, from_model)
    n = len(
        sample_cuts(
            [c for c in all_cuts if c.model == resolved],
            turns=turns,
            seed=seed,
            per_session=per_session,
        )
    )
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
        per_session=per_session,
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


def _pending_targets(
    cuts: list[CutPoint], replays: list[ReplayResult], manifest: dict[str, Any]
) -> list[tuple[CutPoint, str, str]]:
    """(cut, model, role) triples for replays that are missing or have `error` set.

    A role counts as done for a cut as soon as any recorded replay for that (cut, role)
    has no error, regardless of how many earlier attempts for it failed.
    """
    roles: list[tuple[str, str]] = [("candidate", str(manifest["to"]))]
    if manifest["noise"]:
        roles.append(("noise", str(manifest["from"])))
    succeeded: dict[str, set[str]] = {role: set() for role, _ in roles}
    for r in replays:
        if r.role in succeeded and r.error is None:
            succeeded[r.role].add(r.cut_id)
    return [
        (cut, model, role)
        for cut in cuts
        for role, model in roles
        if cut.cut_id not in succeeded[role]
    ]


@main.command()
@click.option(
    "--run",
    "run_id",
    default=None,
    help="Run id (folder name under ~/.claude-drift/runs). Default: latest.",
)
@click.option(
    "--workers",
    default=None,
    type=int,
    help="Replays in flight at once. Default: the run's original --workers.",
)
@click.option("--yes", "-y", is_flag=True, help="Skip the cost confirmation.")
def resume(run_id: str | None, workers: int | None, yes: bool) -> None:
    """Re-run replays that are missing or failed from a previous `drift replay` run.

    Uses your claude login.
    """
    signal.signal(signal.SIGTERM, _raise_sigterm)
    if shutil.which("claude") is None:
        raise click.ClickException(
            "claude binary not found on PATH; install Claude Code and log in first"
        )
    try:
        run = get_run(run_id) if run_id else latest_run()
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    if run is None:
        raise click.ClickException("no runs yet; run `drift replay` first")

    manifest = run.read_manifest()
    cuts = run.read_cuts()
    replays = run.read_replays()
    pending = _pending_targets(cuts, replays, manifest)
    if not pending:
        click.echo("nothing to resume: all replays succeeded")
        return

    resolved_workers = workers if workers is not None else int(manifest["workers"])
    timeout = float(manifest["timeout"])

    est = len(pending) * TOKENS_PER_CUT_ESTIMATE
    click.echo(f"pending replays: {len(pending)}  estimated input tokens: {est:,}")
    if not yes:
        click.confirm("continue?", abort=True)

    started = datetime.now().isoformat()
    manifest["status"] = "running"
    run.write_manifest(manifest)

    state = _execute(
        run, pending, manifest, SubprocessRunner(), resolved_workers, timeout, click.echo
    )
    manifest.setdefault("resumes", []).append(
        {
            "started": started,
            "finished": manifest["finished"],
            "pending": len(pending),
            "completed": state.completed,
            "errors": state.errors,
        }
    )
    run.write_manifest(manifest)

    click.echo(
        f"status: {manifest['status']}  completed: {manifest['completed']}  "
        f"failed replays: {manifest['errors']}"
    )
    click.echo(f"run id: {run.path.name}")
    click.echo()
    click.echo(build_report(run), nl=False)
    if manifest["status"] == "aborted":
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
