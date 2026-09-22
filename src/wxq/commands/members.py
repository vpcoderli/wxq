"""members command — list group chat members.

FIX: Original code had a bug on lines 46-52 where the `m` variable
was referenced outside the for loop, causing only the last member
to appear in text output. This version iterates correctly.
"""

from __future__ import annotations

import click

from ..output.formatter import output


@click.command("members")
@click.argument("group_name")
@click.option("--format", "fmt", default="json", type=click.Choice(["json", "text"]), help="Output format")
@click.pass_context
def members(ctx: click.Context, group_name: str, fmt: str) -> None:
    """List group chat members.

    \b
    Examples:
      wxq members "AI Group"
      wxq members "Group Name" --format text
    """
    app = ctx.obj

    username = app.contacts.resolve_username(group_name)
    if not username:
        click.echo(f"Not found: {group_name}", err=True)
        ctx.exit(1)

    if "@chatroom" not in username:
        click.echo(f"{group_name} is not a group chat", err=True)
        ctx.exit(1)

    names = app.contacts.get_names()
    display_name = names.get(username, username)

    result = app.contacts.get_group_members(username)
    member_dicts = [
        {
            "username": m.username,
            "nick_name": m.nick_name,
            "remark": m.remark,
            "display_name": m.display_name,
        }
        for m in result.members
    ]

    if fmt == "json":
        output({
            "group": display_name,
            "username": username,
            "member_count": result.member_count,
            "owner": result.owner,
            "members": member_dicts,
        }, "json")
    else:
        # FIX: iterate over ALL members (original only processed the last)
        lines = []
        for m in member_dicts:
            line = f"{m['display_name']}  ({m['username']})"
            if m["remark"]:
                line += f"  Remark: {m['remark']}"
            lines.append(line)

        header = f"{display_name} members ({result.member_count} total)"
        if result.owner:
            header += f", owner: {result.owner}"
        output(header + ":\n\n" + "\n".join(lines), "text")
