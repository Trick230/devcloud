"""Keeps a project in step between the machines and web sessions that work on it (dc-hook.sh).

start  (SessionStart) on a PC: fast-forwards the checked-out branch to the web sessions' pushed
       claude/* branches (a web session can only push those). Everywhere: warns when another
       session on the project was active in the last ACTIVE seconds.
claim  (SessionStart, in the background) claims the project in the state repo
       (sessions/<project>.json).
beat   (Stop) refreshes this session's claim, at most every BEAT seconds.
end    (SessionEnd) drops this session's claim.
Each prints only what the agent must know (nothing when all is well): the hook puts it into
the session's context, so a quiet start costs no tokens.
"""
import json
import os
import socket
import time

from devcloud import core

ACTIVE = 2 * 3600      # a claim older than this is a session that died without SessionEnd
BEAT = 600


def where() -> str:
    return "web" if os.environ.get("CLAUDE_CODE_REMOTE") else core.OS


def _rel(p: core.Project) -> str:
    return f"sessions/{p.name}.json"


def _load(path: str) -> dict | None:
    try:
        return json.loads(core.read(path))
    except (OSError, ValueError):
        return None


def handed_off(root: str) -> str | None:
    """The session that handed off to the next one (handoff.py: "status: pending <sid> ..." in the
    project's memory): its claim is the session this one replaces, not another one."""
    from devcloud import claude
    path = os.path.join(core.home(), ".claude", "projects", claude.slug(root), "memory", "handoff.md")
    try:
        with open(path, encoding="utf-8") as f:
            first = f.readline().split()
    except OSError:
        return None
    return first[2] if first[:2] == ["status:", "pending"] and len(first) > 2 else None


def other_active(claim: dict | None, sid: str, now: float, previous: str | None = None) -> str | None:
    """The warning for a claim of another session still within ACTIVE, else None."""
    if not claim or claim.get("session") in (sid, previous):
        return None
    age = now - float(claim.get("time", 0))
    if age >= ACTIVE:
        return None
    return (f"WARNING: another Claude session on this project was active {int(age // 60)} min ago "
            f"({claim.get('where')} on {claim.get('host')}). Tell the user in one line, in your first "
            "reply: two sessions on one project clash on memory and git; finish or close the other one first.")


def _claim(p: core.Project, sid: str) -> None:
    rel = _rel(p)
    data = {"session": sid, "where": where(), "host": socket.gethostname(), "time": int(time.time())}
    core.write(os.path.join(core.STATE, rel), (json.dumps(data) + "\n").encode())
    core.commit_state(f"session: {p.name} {where()}", [rel])


def branches(root: str) -> list[str]:
    """A PC's checked-out branch fast-forwarded to origin/claude/* (web work); diverged ones reported."""
    g = lambda *a: core.git(*a, cwd=root, check=False)
    head = core.out(g("rev-parse", "--abbrev-ref", "HEAD"))
    if not head or head == "HEAD" or head.startswith("claude/"):
        return []
    if g("fetch", "-q", "--prune", "origin").returncode != 0:
        return []
    msgs = []
    refs = core.out(g("for-each-ref", "--format=%(refname:short)", "refs/remotes/origin/claude/")).split()
    for b in refs:
        n = core.out(g("rev-list", "--count", f"HEAD..{b}"))
        if n in ("", "0"):
            continue
        if not any(ln.startswith("+") for ln in core.out(g("cherry", "HEAD", b)).splitlines()):
            continue   # its changes are already in (rebased or cherry-picked by hand)
        if g("merge-base", "--is-ancestor", "HEAD", b).returncode == 0:
            r = g("merge", "-q", "--ff-only", b)
            if r.returncode == 0:
                msgs.append(f"{head} fast-forwarded to {b} ({n} commit(s) from a web session; not pushed yet).")
                continue
            msgs.append(f"{b} has {n} commit(s) from a web session; the fast-forward of {head} failed "
                        f"(local changes in the way?): {(r.stderr or b'').decode(errors='replace').strip()[-200:]}")
        else:
            msgs.append(f"{b} has {n} commit(s) from a web session not in {head}, and the two diverged: "
                        "ask the user before merging or rebasing it (or deleting the remote branch, "
                        "which also silences this).")
    return msgs


def start(sid: str, source: str = "startup") -> list[str]:
    """source: the SessionStart source; a resume, /clear or compaction does not touch git."""
    p = core.project()
    if not p:
        return []
    msgs = [] if os.environ.get("CLAUDE_CODE_REMOTE") or source != "startup" else branches(p.root)
    try:
        core.ensure_state()
    except core.Fail as e:   # the branch news above must still reach the agent
        return msgs + [f"dc session: no session check ({e})"]
    w = other_active(_load(os.path.join(core.STATE, _rel(p))), sid, time.time(), handed_off(p.root))
    if w:
        msgs.append(w)
    return msgs


def claim(sid: str) -> None:
    """start's claim, apart: the hook runs it in the background (a state commit and push)."""
    p = core.project()
    if p:
        core.ensure_state()
        _claim(p, sid)


def beat(sid: str) -> None:
    p = core.project()
    if not p:
        return
    c = _load(os.path.join(core.STATE, _rel(p)))
    if c and c.get("session") == sid and time.time() - float(c.get("time", 0)) < BEAT:
        return       # fresh enough: no state commit on every reply
    core.ensure_state()
    c = _load(os.path.join(core.STATE, _rel(p)))
    if c and c.get("session") not in (None, sid):
        return       # another session took the project over since: leave its claim alone
    _claim(p, sid)


def end(sid: str) -> None:
    p = core.project()
    if not p:
        return
    core.ensure_state()
    rel = _rel(p)
    path = os.path.join(core.STATE, rel)
    c = _load(path)
    if c and c.get("session") == sid:
        os.remove(path)
        core.commit_state(f"session: {p.name} ended", [rel])
