"""Create and manage LANA user accounts from the command line.

    python -m scripts.manage_users add alice
    python -m scripts.manage_users list
    python -m scripts.manage_users passwd alice
    python -m scripts.manage_users role alice admin
    python -m scripts.manage_users disable alice
    python -m scripts.manage_users delete alice

Why a CLI rather than a sign-up page
------------------------------------
Open registration on a data-analysis tool means the first stranger who finds
the port becomes a user. Account creation is therefore something the person
who runs the instance does, on the machine it runs on — which is also the only
place they can prove they are that person without an account system already
existing. The first account created is an admin, because an instance with no
administrator has no way to make one.

Passwords are read from a terminal prompt with echo off, never from an
argument. A password in ``argv`` is visible to every other process on the
machine through the process list and is written to the shell history file.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.accounts import (  # noqa: E402
    ROLES,
    AccountError,
    AccountStore,
    account_db_path,
)


def _prompt_password(confirm: bool = True) -> str:
    """Read a password twice from the terminal, with echo off.

    ``LANA_INITIAL_PASSWORD`` bypasses the prompt for automated provisioning
    (a container entrypoint, a test). It is deliberately awkward and named for
    what it is, rather than being a ``--password`` flag that people would
    reach for by default and leak into their shell history.
    """
    from_env = os.getenv("LANA_INITIAL_PASSWORD")
    if from_env:
        return from_env

    if not sys.stdin.isatty():
        raise SystemExit(
            "No terminal to prompt on. Set LANA_INITIAL_PASSWORD for "
            "non-interactive provisioning."
        )

    password = getpass.getpass("Password: ")
    if confirm and password != getpass.getpass("Confirm password: "):
        raise SystemExit("The passwords did not match. Nothing was changed.")
    return password


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.manage_users",
        description="Manage LANA user accounts.",
    )
    parser.add_argument(
        "--db", default=None,
        help="Account database path (default: $LANA_DATA_DIR/accounts.db).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="Create a user.")
    add.add_argument("username")
    add.add_argument("--role", choices=ROLES, default=None,
                     help="Ignored for the first user, who is always an admin.")

    sub.add_parser("list", help="List users.")

    passwd = sub.add_parser("passwd", help="Change a password (ends their sessions).")
    passwd.add_argument("username")

    role = sub.add_parser("role", help="Change a role.")
    role.add_argument("username")
    role.add_argument("role", choices=ROLES)

    disable = sub.add_parser("disable", help="Disable a user and end their sessions.")
    disable.add_argument("username")

    enable = sub.add_parser("enable", help="Re-enable a disabled user.")
    enable.add_argument("username")

    delete = sub.add_parser("delete", help="Delete a user.")
    delete.add_argument("username")

    args = parser.parse_args(argv)
    store = AccountStore(Path(args.db) if args.db else account_db_path())

    try:
        return _dispatch(args, store)
    except AccountError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _dispatch(args, store: AccountStore) -> int:
    if args.command == "add":
        first = store.count() == 0
        user = store.create_user(args.username, _prompt_password(), args.role)
        print(f"Created '{user.username}' ({user.role}).")
        if first:
            print("This is the first account, so it is an administrator.")
        print("Start LANA with LANA_ACCOUNTS=true for sign-in to be required.")
        return 0

    if args.command == "list":
        users = store.list_users()
        if not users:
            print("No users yet. Create one with: "
                  "python -m scripts.manage_users add <username>")
            return 0
        width = max(len(u.username) for u in users)
        for user in users:
            state = " (disabled)" if user.disabled else ""
            print(f"{user.username:<{width}}  {user.role}{state}")
        return 0

    if args.command == "passwd":
        store.set_password(args.username, _prompt_password())
        print(f"Password changed for '{args.username}'. "
              f"Their existing sign-ins were ended.")
        return 0

    if args.command == "role":
        store.set_role(args.username, args.role)
        print(f"'{args.username}' is now {args.role}.")
        return 0

    if args.command in ("disable", "enable"):
        store.set_disabled(args.username, args.command == "disable")
        print(f"'{args.username}' is now {args.command}d.")
        return 0

    if args.command == "delete":
        store.delete_user(args.username)
        print(f"Deleted '{args.username}'. Their sessions are gone; any data "
              f"they uploaded is still on disk until it expires.")
        return 0

    raise AssertionError(f"unhandled command {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())
