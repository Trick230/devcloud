"""The agent setup that makes Claude Code work, in the state repo under claude/:

  global/claude/  ~/.claude: CLAUDE.md, agents/, tools/ (scripts), themes/, keybindings.json,
                  statusline-command.sh
  os/<os>/        settings that hold this OS's paths: ~/.claude/settings.json
  memory/<proj>/  ~/.claude/projects/<slug of the project path>/memory/ (the slug differs per
                  machine, the project name does not)
  projects/<proj>/ the project's agent files that git does not track (CLAUDE.md,
                  CLAUDE.local.md, .claude/, .agents/)

Never credentials (.credentials.json), sessions, history or caches. Each file is
merged three-way against what this machine last synced (.dc/sync.json): a side that did not
change takes the other; a file changed on both sides keeps the local copy and writes the
stored one next to it as <name>.dc-remote (on an ephemeral machine, a file it never synced
takes the stored copy). Merge what you need from the .dc-remote copy into the file, delete the
.dc-remote copy, and the next push stores your version. Deleted files are not propagated.
Files that run code (settings.json, tools, hooks) are applied only when a machine holding the
age key stored them (state signed.json): a GitHub token alone cannot plant a hook.
"""
from __future__ import annotations

import os
import re
import subprocess
from functools import lru_cache

from . import core
from .core import say

GLOBAL = {   # state prefix: (home subdir, files, dirs (recursive), file patterns in dirs)
    "global/claude": (".claude", ["CLAUDE.md", "keybindings.json", "statusline-command.sh"],
                      ["agents", "tools", "themes"]),
}
PER_OS = {"claude": (".claude", ["settings.json"])}
PROJECT_FILES = ["CLAUDE.md", "CLAUDE.local.md"]
PROJECT_DIRS = [".claude", ".agents"]
SKIP = re.compile(r"(^|/)(__pycache__|node_modules)(/|$)|\.pyc$|(^|/)settings\.local\.json$"
                  r"|\.dc-remote$|(^|/)\.credentials\.json$")


def slug(path: str) -> str:
    """Claude Code's folder name for a project path."""
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.normpath(path))


def _walk(base: str, rel_dir: str) -> list[str]:
    top = os.path.join(base, rel_dir)
    res = []
    for d, dirs, files in os.walk(top):
        dirs[:] = [x for x in dirs if x not in ("__pycache__", "node_modules", ".git")]
        for f in files:
            res.append(os.path.relpath(os.path.join(d, f), base).replace(os.sep, "/"))
    return res


@lru_cache(maxsize=None)
def _tracked(root: str) -> frozenset[str]:
    r = subprocess.run(["git", "-C", root, "ls-files", "-z"], capture_output=True)
    return frozenset(r.stdout.decode(errors="replace").split("\0")) if r.returncode == 0 else frozenset()


def pairs(projects: list[core.Project]) -> dict[str, str]:
    """{state path: local path} of everything synced from this machine."""
    h, m = core.home(), {}
    for prefix, (sub, files, dirs) in GLOBAL.items():
        base = os.path.join(h, sub)
        rels = [f for f in files] + [r for d in dirs for r in _walk(base, d)]
        for r in rels:
            m[f"claude/{prefix}/{r}"] = os.path.join(base, *r.split("/"))
    for name, (sub, files) in PER_OS.items():
        for f in files:
            m[f"claude/os/{core.OS}/{name}/{f}"] = os.path.join(h, sub, f)
    for p in projects:
        mem = os.path.join(h, ".claude", "projects", slug(p.root), "memory")
        for r in _walk(mem, "."):
            m[f"claude/memory/{p.name}/{r.removeprefix('./')}"] = os.path.join(mem, *r.split("/"))
        tracked = _tracked(p.root)
        rels = PROJECT_FILES + [r for d in PROJECT_DIRS for r in _walk(p.root, d)]
        for r in rels:
            if r not in tracked:
                m[f"claude/projects/{p.name}/{r}"] = os.path.join(p.root, *r.split("/"))
    return {k: v for k, v in m.items() if not SKIP.search(k)}


def _exclude(root: str, rel: str) -> None:
    """Keep a restored agent file out of git: its top entry goes into .git/info/exclude."""
    top = rel.split("/")[0] + ("/" if "/" in rel else "")
    r = subprocess.run(["git", "-C", root, "rev-parse", "--git-path", "info/exclude"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return
    path = os.path.join(root, r.stdout.strip())
    text = open(path).read() if os.path.isfile(path) else ""
    if top not in text.splitlines():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            f.write(("\n" if text and not text.endswith("\n") else "") + top + "\n")


def allowed(parts: list[str]) -> bool:
    """A state path claude push could have produced: anything else in the state repo (written
    by someone else) never becomes a file here (~/.ssh, a project's .git/hooks, ...)."""
    if any(x in ("", ".", "..") or "\\" in x or ":" in x for x in parts) or SKIP.search("/".join(parts)):
        return False
    if parts[:3] == ["claude", "global", "claude"] and len(parts) > 3:
        files, dirs = GLOBAL["global/claude"][1:]
        return (len(parts) == 4 and parts[3] in files) or (len(parts) > 4 and parts[3] in dirs)
    if parts[:2] == ["claude", "os"] and len(parts) == 5:
        return parts[3] in PER_OS and parts[4] in PER_OS[parts[3]][1]
    if parts[:2] == ["claude", "memory"] and len(parts) > 3:
        return True
    if parts[:2] == ["claude", "projects"] and len(parts) > 3:
        return (len(parts) == 4 and parts[3] in PROJECT_FILES) or (len(parts) > 4 and parts[3] in PROJECT_DIRS)
    return False


def _from_state(rel: str, projects: dict[str, core.Project]) -> str | None:
    """Where a stored file goes on this machine (None: not here)."""
    h = core.home()
    parts = rel.split("/")
    if not allowed(parts):
        return None
    if parts[:2] == ["claude", "global"] and len(parts) > 3 and parts[2] == "claude":
        return os.path.join(h, "." + parts[2], *parts[3:])
    if parts[:2] == ["claude", "os"] and len(parts) > 4:
        return os.path.join(h, "." + parts[3], *parts[4:]) if parts[2] == core.OS else None
    if parts[:2] == ["claude", "memory"] and len(parts) > 3 and parts[2] in projects:
        return os.path.join(h, ".claude", "projects", slug(projects[parts[2]].root), "memory", *parts[3:])
    if parts[:2] == ["claude", "projects"] and len(parts) > 3 and parts[2] in projects:
        p = projects[parts[2]]
        return None if "/".join(parts[3:]) in _tracked(p.root) else os.path.join(p.root, *parts[3:])
    return None


def resolved(rel: str, local: str, sh: str | None, sync: dict) -> bool:
    """A conflict merged by hand: the .dc-remote copy pull wrote for this stored version is gone."""
    return sync.get(rel + "#conflict") == sh and not os.path.exists(local + ".dc-remote")


def _key() -> bytes | None:
    try:
        return core.mac_key()
    except core.Fail as e:
        say(f"claude: no age key, nothing signed or taken from the state repo ({e})")
        return None


def push(quiet: bool = False) -> None:
    core.ensure_state()
    projects = core.known_projects()
    sync = core.load_sync()
    key = _key()
    changed, conflicts = [], []
    for rel, local in sorted(pairs(projects).items()):
        stored = os.path.join(core.STATE, *rel.split("/"))
        lh, sh = core.file_sha(local), core.file_sha(stored)
        what = core.decide(lh, sync.get(rel), sh)
        if what == "conflict" and resolved(rel, local, sh, sync):
            what = "take_local"
        if what == "take_local":
            core.write(stored, core.read(local))
            changed.append(rel)
            sync[rel] = lh
            sync.pop(rel + "#conflict", None)
        elif what == "same" and lh:
            sync[rel] = lh
        elif what == "conflict":
            conflicts.append(rel)
        if key and what in ("take_local", "same") and lh:
            sig = core.sign(rel, key)        # this machine's own copy: vouched for
            if sig:
                changed.append(sig)
    if changed:
        core.commit_state(f"claude: {len(changed)} file(s)", changed)
    core.save_sync(sync)
    for c in conflicts:
        say(f"conflict (changed here and elsewhere; dc claude pull, merge, delete the .dc-remote): {c}")
    if not quiet or changed:
        say(f"claude push: {len(changed)} file(s) stored, {len(conflicts)} conflict(s)")


def pull(quiet: bool = False) -> None:
    core.ensure_state()
    projects = {p.name: p for p in core.known_projects()}
    sync = core.load_sync()
    key = _key()
    base = os.path.join(core.STATE, "claude")
    written, conflicts, code, unsigned = 0, 0, [], []
    for rel in sorted("claude/" + r for r in _walk(base, ".") if not SKIP.search(r)):
        rel = rel.replace("claude/./", "claude/")
        local = _from_state(rel, projects)
        if not local:
            continue
        stored = os.path.join(core.STATE, *rel.split("/"))
        lh, sh = core.file_sha(local), core.file_sha(stored)
        what = core.decide(lh, sync.get(rel), sh)
        if what == "conflict" and core.EPHEMERAL and rel not in sync:
            what = "take_state"   # a fresh sandbox's own defaults (settings.json) give way to the stored setup
        if what in ("take_state", "conflict") and not (key and core.signed(rel, key)):
            unsigned.append(rel.removeprefix("claude/"))
            continue
        if what == "conflict" and resolved(rel, local, sh, sync):
            continue                # merged by hand: the next push stores it
        if what == "take_state":
            core.write(local, core.read(stored))
            if rel.startswith("claude/projects/"):
                parts = rel.split("/")
                _exclude(projects[parts[2]].root, "/".join(parts[3:]))
            sync[rel] = sh
            written += 1
            if runs_code(rel):
                code.append(rel.removeprefix("claude/"))
        elif what == "same":
            sync[rel] = sh
        elif what == "conflict":
            core.write(local + ".dc-remote", core.read(stored))
            sync[rel + "#conflict"] = sh
            say(f"conflict: kept {local}, the stored copy is in {local}.dc-remote")
            conflicts += 1
    core.save_sync(sync)
    if not quiet or written or conflicts:
        say(f"claude pull: {written} file(s) updated, {conflicts} conflict(s)")
    if code:   # hooks, tools, settings: always shown
        say(f"claude pull: code from the state repo (hooks, tools): {', '.join(code)}")
    if unsigned:   # stored by something without the age key: never applied
        say(f"claude pull: NOT applied, stored without the age key (read the state repo's history; "
            f"`dc trust` if it was you): {', '.join(unsigned)}")


def runs_code(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    return name == "settings.json" or "/tools/" in rel or "/hooks/" in rel
