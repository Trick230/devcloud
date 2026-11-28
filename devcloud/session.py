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
import re
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


def merged(g, b: str, up: str) -> bool:
    """b's work is already in up (the pushed branch, not just this clone): contained, every
    commit there by rebase or cherry-pick, or no net change at all (commits that undo each other)."""
    if g("merge-base", "--is-ancestor", b, up).returncode == 0:
        return True
    r = g("cherry", up, b)
    if r.returncode == 0 and not any(ln.startswith("+") for ln in core.out(r).splitlines()):
        return True
    base = core.out(g("merge-base", up, b))
    return bool(base) and g("diff", "--quiet", base, b).returncode == 0


START = "dc-start"   # in .git: how many HEAD reflog entries there were when a web session started
MADE = re.compile(r"(commit|cherry-pick|revert)\b|.*\((pick|squash|fixup|reword|edit|continue)\)")


def _start_file(root: str) -> str:
    rel = core.out(core.git("rev-parse", "--git-path", START, cwd=root, check=False))
    return os.path.join(root, rel or os.path.join(".git", START))


def _reflog(root: str) -> list[tuple[str, str]]:
    """HEAD's reflog, newest first: (sha, what made it)."""
    r = core.git("reflog", "show", "--format=%H%x09%gs", "HEAD", "--", cwd=root, check=False)
    return [(ln.partition("\t")[0], ln.partition("\t")[2])
            for ln in core.out(r).splitlines()] if r.returncode == 0 else []


def mark_start(root: str) -> None:
    """Web, at SessionStart: everything before this was not made by this session."""
    core.write(_start_file(root), f"{len(_reflog(root))}\n".encode())


def web_commits(root: str) -> list[str]:
    """The commits this web session made itself (commit, amend, merge, cherry-pick, a rebase's
    picks) on its claude/* branches, from HEAD's reflog since it started. Not what a pull or a
    rebase brought in: anyone with the GitHub token alone can push those. Without the start mark
    (an older devcloud started it) nothing: the PC then only reports the branch."""
    try:
        n = int(core.read(_start_file(root)).decode().strip())
    except (OSError, ValueError):
        return []
    log = _reflog(root)
    made = {sha for sha, what in log[:max(len(log) - n, 0)] if MADE.match(what)}
    g = lambda *a: core.git(*a, cwd=root, check=False)  # noqa: E731
    heads = core.out(g("for-each-ref", "--format=%(objectname)", "refs/heads/claude/")).split()
    if not made or not heads:
        return []
    reach = core.out(g("rev-list", "--max-count=2000", *heads)).split()
    return [c for c in reach if c in made]


def vouch_web(p: core.Project) -> None:
    if os.environ.get("CLAUDE_CODE_REMOTE"):
        try:
            core.vouch(p.name, web_commits(p.root), kind="web")
        except Exception:   # a hook: no key, state or network just means the PC asks first
            pass


def branches(root: str, trusted=lambda sha: True) -> list[str]:
    """A PC's checked-out branch fast-forwarded to origin/claude/* (web work); diverged ones reported;
    the ones whose work is already in the pushed branch deleted from origin (the PC can, the web can't).
    trusted(sha): a web session holding the age key vouched for that commit (see vouch_web); anyone
    with the GitHub token alone can push a claude/* branch, so one bringing in any other commit is
    only reported."""
    g = lambda *a: core.git(*a, cwd=root, check=False)
    head = core.out(g("rev-parse", "--abbrev-ref", "HEAD"))
    if not head or head == "HEAD" or head.startswith("claude/"):
        return []
    if g("fetch", "-q", "--prune", "origin").returncode != 0:
        return []
    msgs = []
    refs = core.out(g("for-each-ref", "--format=%(refname:short)", "refs/remotes/origin/claude/")).split()
    up = f"origin/{head}"
    has_up = g("rev-parse", "-q", "--verify", up).returncode == 0
    for b in refs:
        if has_up and merged(g, b, up):
            name = b.removeprefix("origin/")
            if g("push", "-q", "origin", "--delete", name).returncode == 0:
                msgs.append(f"deleted the remote branch {name}: its work is already in {up}.")
                continue
        n = core.out(g("rev-list", "--count", f"HEAD..{b}"))
        if n in ("", "0"):
            continue
        if not any(ln.startswith("+") for ln in core.out(g("cherry", "HEAD", b)).splitlines()):
            continue   # its changes are already in (rebased or cherry-picked by hand)
        bad = [c for c in core.out(g("rev-list", f"HEAD..{b}")).split() if not trusted(c)]
        if bad:
            msgs.append(f"{b}: {len(bad)} of its {n} new commit(s) no web session holding the age key "
                        f"vouched for (pushed with the GitHub token alone, or by an older devcloud): not merged. "
                        f"Tell the user; review `git log -p HEAD..{b}` with them before merging it by hand, "
                        f"or delete it: git push origin --delete {b.removeprefix('origin/')}")
            continue
        if g("merge-base", "--is-ancestor", "HEAD", b).returncode == 0:
            r = g("merge", "-q", "--ff-only", b)
            if r.returncode == 0:
                msgs.append(f"{head} fast-forwarded to {b} ({n} commit(s) from a web session; not pushed yet).")
                continue
            msgs.append(f"{b} has {n} commit(s) from a web session; the fast-forward of {head} failed "
                        f"(local changes in the way?): {(r.stderr or b'').decode(errors='replace').strip()[-200:]}")
        else:
            own = core.out(g("log", "--format=%h %s", "--right-only", "--cherry-pick", "-n", "6",
                             f"HEAD...{b}")).splitlines()
            lost = "; ".join(own[:5]) + (" ..." if len(own) > 5 else "")
            msgs.append(f"{b} has {n} commit(s) from a web session not in {head}, and the two diverged. "
                        f"Its own commits: {lost}. Ask the user: merge or rebase it into {head}, or delete "
                        f"the branch (those commits are lost): git push origin --delete {b.removeprefix('origin/')}")
    return msgs


SANDBOX_AUTHORS = ("", "claude")   # a web sandbox's default git identity (Claude <noreply@anthropic.com>)


def identity(name: str, login: str, uid: str, env: dict) -> tuple[str, str] | None:
    """The git identity a web session should commit as: DC_GIT_NAME/DC_GIT_EMAIL, else the
    token's GitHub account (its noreply address, which GitHub attributes to it); None when the
    sandbox's identity was already replaced or the account is unknown."""
    if name.strip().lower() not in SANDBOX_AUTHORS or not (login or env.get("DC_GIT_NAME")):
        return None
    return (env.get("DC_GIT_NAME") or login,
            env.get("DC_GIT_EMAIL") or f"{uid}+{login}@users.noreply.github.com")


def git_identity() -> None:
    """Web: commits go out as the GitHub account, not as the sandbox's Claude identity."""
    g = lambda *a: core.out(core.run(["git", "config", "--global", *a], check=False))  # noqa: E731
    name = g("user.name")
    if name.strip().lower() not in SANDBOX_AUTHORS:
        return
    r = core.gh("api", "user", "-q", '.login + " " + (.id|tostring)', check=False)
    login, _, uid = core.out(r).partition(" ") if r.returncode == 0 else ("", "", "")
    who = identity(name, login, uid, dict(os.environ))
    if who:
        g("user.name", who[0])
        g("user.email", who[1])
        # the sandbox signs with a key GitHub verifies only for its own identity: under the
        # account's the commits would show "Unverified", so they go out unsigned, like a PC's
        g("commit.gpgsign", "false")


def start(sid: str, source: str = "startup") -> list[str]:
    """source: the SessionStart source; a resume, /clear or compaction does not touch git.
    DC_STATE_PULLED=1: the caller has just pulled the state repo (dc-hook.sh runs dc claude pull
    first), so reading it needs no second pull (~1 s). An environment variable, not a flag, so a
    newer hook still works with an older dc."""
    pulled = os.environ.get("DC_STATE_PULLED") == "1"
    if os.environ.get("CLAUDE_CODE_REMOTE") and source == "startup":
        git_identity()
    p = core.project()
    if not p:
        return []
    if os.environ.get("CLAUDE_CODE_REMOTE") and source == "startup" and not os.path.exists(_start_file(p.root)):
        mark_start(p.root)
    state: list = []   # [None] once the state repo is fresh, [the Fail] when that failed
    keys: list = []

    def fresh() -> core.Fail | None:
        if not state:
            try:
                if not (pulled and os.path.isdir(os.path.join(core.STATE, ".git"))):
                    core.ensure_state()
                state.append(None)
            except core.Fail as e:
                state.append(e)
        return state[0]

    def trusted(sha: str) -> bool:
        try:
            if fresh() is not None:
                return False
            if not keys:
                keys.append(core.mac_key())
            return core.vouched(p.name, sha, keys[0], kind="web")
        except core.Fail:
            return False

    msgs = [] if os.environ.get("CLAUDE_CODE_REMOTE") or source != "startup" else branches(p.root, trusted)
    e = fresh()
    if e:   # the branch news above must still reach the agent
        return msgs + [f"dc session: no session check ({e})"]
    w = other_active(_load(os.path.join(core.STATE, _rel(p))), sid, time.time(), handed_off(p.root))
    if w:
        msgs.append(w)
    w = core.token_warning(core.token_expiry(), time.time())   # cached: claim refreshes it
    if w:
        msgs.append(w)
    return msgs


def claim(sid: str) -> None:
    """start's claim, apart: the hook runs it in the background (a state commit and push)."""
    p = core.project()
    if p:
        core.ensure_state()
        _claim(p, sid)
    core.token_expiry(refresh=True)


def beat(sid: str) -> None:
    p = core.project()
    if not p:
        return
    vouch_web(p)
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
    vouch_web(p)
    core.ensure_state()
    rel = _rel(p)
    path = os.path.join(core.STATE, rel)
    c = _load(path)
    if c and c.get("session") == sid:
        os.remove(path)
        core.commit_state(f"session: {p.name} ended", [rel])
