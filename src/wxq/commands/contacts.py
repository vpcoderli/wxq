"""contacts command — search or view contact details."""

from __future__ import annotations

import click

from ..output.formatter import output


@click.command("contacts")
@click.option("--query", default="", help="Search keyword (matches nickname, remark, wxid)")
@click.option("--detail", default=None, help="View contact details (nickname/remark/wxid)")
@click.option("--limit", default=50, help="Number of results")
@click.option("--format", "fmt", default="json", type=click.Choice(["json", "text"]), help="Output format")
@click.pass_context
def contacts(ctx: click.Context, query: str, detail: str | None, limit: int, fmt: str) -> None:
    """Search or list contacts.

    \b
    Examples:
      wxq contacts --query "Li"              # Search contacts
      wxq contacts --detail "Alice"           # View contact details
      wxq contacts --detail "wxid_xxx"        # Lookup by wxid
    """
    app = ctx.obj

    if detail:
        _show_detail(app, detail, fmt)
        return

    full = app.contacts.get_contacts()

    if query:
        q_lower = query.lower()
        matched = [
            c for c in full
            if q_lower in (c.nick_name or "").lower()
            or q_lower in (c.remark or "").lower()
            or q_lower in (c.username or "").lower()
        ]
    else:
        matched = full

    matched = matched[:limit]
    serialized = [
        {
            "username": c.username,
            "nick_name": c.nick_name,
            "remark": c.remark,
            "display_name": c.display_name,
            "is_group": c.is_group,
            "is_subscription": c.is_subscription,
        }
        for c in matched
    ]

    if fmt == "json":
        output(serialized, "json")
    else:
        header = f"Found {len(serialized)} contacts:"
        lines = []
        for c in serialized:
            line = f"{c['display_name']}  ({c['username']})"
            if c["remark"]:
                line += f"  Remark: {c['remark']}"
            lines.append(line)
        output(header + "\n\n" + "\n".join(lines), "text")


def _show_detail(app: object, name_or_id: str, fmt: str) -> None:
    """Show contact details."""
    username = app.contacts.resolve_username(name_or_id)  # type: ignore[attr-defined]
    if not username:
        username = name_or_id

    info = app.contacts.get_contact_detail(username)  # type: ignore[attr-defined]
    if not info:
        click.echo(f"Contact not found: {name_or_id}", err=True)
        return

    info_dict = info.to_dict() if hasattr(info, "to_dict") else info

    if fmt == "json":
        output(info_dict, "json")
    else:
        lines = [f"Contact detail: {info_dict.get('nick_name', '')}"]
        if info_dict.get("remark"):
            lines.append(f"Remark: {info_dict['remark']}")
        if info_dict.get("alias"):
            lines.append(f"WeChat ID: {info_dict['alias']}")
        lines.append(f"wxid: {info_dict.get('username', '')}")
        if info_dict.get("description"):
            lines.append(f"Bio: {info_dict['description']}")
        if info_dict.get("is_group"):
            lines.append("Type: Group")
        elif info_dict.get("is_subscription"):
            lines.append("Type: Subscription Account")
        elif info_dict.get("verify_flag") and info_dict["verify_flag"] >= 8:
            lines.append("Type: Verified Enterprise")
        if info_dict.get("avatar"):
            lines.append(f"Avatar: {info_dict['avatar']}")
        output("\n".join(lines), "text")
