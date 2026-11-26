"""Claude Code accounts on this machine: save the signed-in one, switch without the browser.

Only two things belong to the account: the token (<config>/.credentials.json) and the
`oauthAccount` entry of .claude.json. Everything else (settings, memory, sessions, hooks, the
state repo) is shared, so a switch keeps the whole workflow. Saved accounts live in
.dc/accounts/<name>/ on this machine only: the refresh token rotates, so a copy used on two
machines would break one of them. Never synced, committed or printed.

A switch first stores the active account's current token back into its own slot (it may have
refreshed since the save), then puts the other one in place. Sessions already running keep
the account they started with; new sessions (and `claude --resume`) use the new one.
"""
from __future__ import annotations

import json
import os
import re

from . import core
from .core import Fail, say

NAME = re.compile(r"^[A-Za-z0-9._-]+$")


def config_dir() -> str:
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(core.home(), ".claude")


def _creds() -> str:
    return os.path.join(config_dir(), ".credentials.json")


def _config() -> str:
    """.claude.json: inside CLAUDE_CONFIG_DIR when it is set, else in the home folder."""
    d = os.environ.get("CLAUDE_CONFIG_DIR")
    return os.path.join(d, ".claude.json") if d else os.path.join(core.home(), ".claude.json")


def _slots() -> str:
    return os.environ.get("DC_ACCOUNTS") or os.path.join(core.DC, "accounts")   # DC_ACCOUNTS: tests


def _load(path: str) -> dict:
    try:
        return json.loads(core.read(path))
    except (OSError, ValueError):
        return {}


def _active() -> dict:
    return _load(_config()).get("oauthAccount") or {}


def saved() -> dict[str, dict]:
    """{name: oauthAccount} of the saved accounts."""
    d = _slots()
    res = {}
    for n in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        acc = _load(os.path.join(d, n, "account.json"))
        if acc and os.path.isfile(os.path.join(d, n, "credentials.json")):
            res[n] = acc
    return res


def active_name() -> str | None:
    uuid = _active().get("accountUuid")
    return next((n for n, a in saved().items() if uuid and a.get("accountUuid") == uuid), None)


def _store(name: str) -> None:
    acc = _active()
    if not acc.get("accountUuid") or not os.path.isfile(_creds()):
        raise Fail("no signed-in account here (claude, then /login)")
    d = os.path.join(_slots(), name)
    core.write(os.path.join(d, "credentials.json"), core.read(_creds()), private=True)
    core.write(os.path.join(d, "account.json"), json.dumps(acc, indent=1).encode(), private=True)


def save(name: str) -> None:
    if not NAME.match(name):
        raise Fail(f"{name}: letters, digits, . _ - only")
    uuid = _active().get("accountUuid")
    other = next((n for n, a in saved().items() if n != name and a.get("accountUuid") == uuid), None)
    if other:
        raise Fail(f"this account is already saved as {other}")
    _store(name)
    say(f"saved the signed-in account ({_active().get('emailAddress', '?')}) as {name}")


def use(name: str) -> None:
    accs = saved()
    if name not in accs:
        raise Fail(f"no saved account {name} (dc account lists them)")
    cur = active_name()
    if cur == name:
        say(f"{name} is already active")
        return
    if cur:
        _store(cur)                      # its token may have rotated since it was saved
    elif _active().get("accountUuid"):
        say("the signed-in account is not saved: `dc account save NAME` first to keep it")
        raise Fail("not switching away from an unsaved account")
    d = os.path.join(_slots(), name)
    cfg = _load(_config())
    cfg["oauthAccount"] = accs[name]
    tmp = _config() + ".dc-tmp"
    core.write(tmp, json.dumps(cfg, indent=2).encode())
    os.replace(tmp, _config())
    core.write(_creds(), core.read(os.path.join(d, "credentials.json")), private=True)
    say(f"active: {name} ({accs[name].get('emailAddress', '?')}); sessions already open keep "
        "their account, new ones and `claude --resume` use this one")


def after(name: str | None) -> str | None:
    """The saved account after `name`, in name order (the next one to switch to)."""
    names = list(saved())
    if not names or names == [name]:
        return None
    return names[(names.index(name) + 1) % len(names)] if name in names else names[0]


def show() -> None:
    accs, cur = saved(), active_name()
    for n, a in accs.items():
        print(f"{'*' if n == cur else ' '} {n:12} {a.get('emailAddress', '?')}  "
              f"{a.get('organizationRateLimitTier') or a.get('billingType') or ''}")
    if not cur:
        a = _active()
        print(f"  active: {a.get('emailAddress', 'none')} (not saved: dc account save NAME)")
