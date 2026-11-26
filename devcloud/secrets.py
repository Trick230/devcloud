"""Each project's .env, age-encrypted in the state repo (secrets/<project>.env.age), plus a
global one (secrets/_global.env.age: the Google Drive remote and anything every project
needs). Lines after a `# dc: local` line stay on this machine (paths and the like).
"""
from __future__ import annotations

import os

from . import core
from .core import Fail, say

MARKER = "# dc: local"
GLOBAL = "_global"


def split(text: str) -> tuple[str, str]:
    """(synced part, local part from the marker on)."""
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.strip() == MARKER:
            return "".join(lines[:i]), "".join(lines[i:])
    return text, ""


def parse(text: str) -> dict[str, str]:
    env = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        env[k] = v
    return env


def _state_file(name: str) -> str:
    return os.path.join(core.STATE, "secrets", f"{name}.env.age")


def state_text(name: str) -> str | None:
    p = _state_file(name)
    return core.decrypt(core.read(p)).decode() if os.path.isfile(p) else None


def _env_path(p: core.Project) -> str:
    return os.path.join(p.root, ".env")


def _local(name: str, p: core.Project | None) -> str:
    if name == GLOBAL:
        return ""
    try:
        return core.read(_env_path(p)).decode()
    except OSError:
        return ""


def _hash(text: str | None) -> str | None:
    return core.sha(text.encode()) if text else None


def store(name: str, text: str) -> None:
    core.write(_state_file(name), core.encrypt(text.encode()))
    core.commit_state(f"secrets {name}", [os.path.relpath(_state_file(name), core.STATE)])


def pull(p: core.Project, force: bool = False) -> None:
    synced, local = split(_local(p.name, p))
    remote = state_text(p.name)
    if remote is None:
        say(f"no secrets stored for {p.name}")
        return
    sync = core.load_sync()
    key = f"secrets:{p.name}"
    what = core.decide(_hash(synced), sync.get(key), _hash(remote))
    if what == "same":
        sync[key] = _hash(remote)
    elif what == "take_local" and not force:
        say(f"{p.name}: the local .env is newer (dc secrets push)")
        return
    elif what == "conflict" and not force:
        raise Fail(f"{p.name}: .env changed here and in the state repo; --force takes the stored one")
    else:
        text = remote + (("" if remote.endswith("\n") else "\n") + local if local else "")
        core.write(_env_path(p), text.encode(), private=True)
        sync[key] = _hash(remote)
        say(f"{p.name}: .env updated")
    core.save_sync(sync)


def push(p: core.Project, force: bool = False) -> None:
    synced, _ = split(_local(p.name, p))
    if not synced.strip():
        raise Fail(f"{p.name}: no .env (or only its local part) to store")
    sync = core.load_sync()
    key = f"secrets:{p.name}"
    what = core.decide(_hash(synced), sync.get(key), _hash(state_text(p.name)))
    if what == "conflict" and not force:
        raise Fail(f"{p.name}: the stored .env changed since the last pull; dc secrets pull first "
                   "(or --force)")
    if what in ("take_local", "conflict"):
        store(p.name, synced)
        say(f"{p.name}: .env stored")
    sync[key] = _hash(synced)
    core.save_sync(sync)


def set_global(pairs: dict[str, str | None]) -> None:
    """Sets (or with None removes) keys of the global env."""
    lines = (state_text(GLOBAL) or "").splitlines()
    out, done = [], set()
    for line in lines:
        k = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if k in pairs:
            done.add(k)
            if pairs[k] is not None:
                out.append(f"{k}={pairs[k]}")
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in pairs.items() if k not in done and v is not None]
    store(GLOBAL, "\n".join(out) + "\n")


def env_for(p: core.Project | None) -> dict[str, str]:
    """The global env, then the project's (stored copy) on top."""
    env = parse(state_text(GLOBAL) or "")
    if p:
        env.update(parse(state_text(p.name) or ""))
    return env


def to_github(p: core.Project) -> None:
    """The global and project env as one Actions secret, DC_ENV, of the project's repo."""
    if not p.repo:
        raise Fail(f"{p.name}: no GitHub origin")
    body = "".join(f"{k}={v}\n" for k, v in env_for(p).items())
    if not body:
        raise Fail("nothing to send (no global or project secrets stored)")
    core.gh("secret", "set", "DC_ENV", "-R", p.repo, input=body.encode())
    say(f"{p.repo}: Actions secret DC_ENV set ({len(body.splitlines())} keys)")
