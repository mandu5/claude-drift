from __future__ import annotations

from collections import Counter

import click

from claude_drift import __version__
from claude_drift.ingest import ingest, projects_root


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
