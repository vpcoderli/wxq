"""CLI entry point — Click command group with all subcommands."""

from __future__ import annotations

import sys

import click

from . import __version__
from .core.context import AppContext


@click.group()
@click.version_option(version=__version__, prog_name="wxq")
@click.option(
    "--config", "config_path", default=None,
    envvar=["WXQ_CONFIG", "WECHAT_QUERY_CONFIG"],
    help="config.json path (auto-detect if omitted)",
)
@click.pass_context
def cli(ctx: click.Context, config_path: str | None) -> None:
    """WeChat Query — query local WeChat messages, contacts, and more.

    \b
    Getting started:
      wxq init                                # First time: extract keys
      wxq sessions                            # Recent sessions
      wxq sessions --limit 10                 # Last 10 sessions
      wxq history "Alice" --limit 20          # Alice's last 20 messages
      wxq history "Work Group" --start-time "2026-04-01"
      wxq search "Claude" --chat "AI Group"   # Search in a group
      wxq search "hello" --limit 50           # Global search
      wxq contacts --query "Li"               # Search contacts
      wxq new-messages                        # Incremental new messages
    """
    # init and version don't need AppContext
    if ctx.invoked_subcommand in ("init", "version"):
        return

    try:
        ctx.obj = AppContext(config_path)
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Initialization failed: {e}", err=True)
        sys.exit(1)


# Register subcommands
from .commands.init import init  # noqa: E402
from .commands.sessions import sessions  # noqa: E402
from .commands.history import history  # noqa: E402
from .commands.search import search  # noqa: E402
from .commands.contacts import contacts  # noqa: E402
from .commands.new_messages import new_messages  # noqa: E402
from .commands.members import members  # noqa: E402
from .commands.export import export  # noqa: E402
from .commands.stats import stats  # noqa: E402
from .commands.unread import unread  # noqa: E402
from .commands.favorites import favorites  # noqa: E402

cli.add_command(init)
cli.add_command(sessions)
cli.add_command(history)
cli.add_command(search)
cli.add_command(contacts)
cli.add_command(new_messages)
cli.add_command(members)
cli.add_command(export)
cli.add_command(stats)
cli.add_command(unread)
cli.add_command(favorites)


def main() -> None:
    """Package entry point."""
    cli()


if __name__ == "__main__":
    main()
