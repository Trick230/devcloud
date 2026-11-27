"""CPU jobs on GitHub Actions, in the project's own repo (public repos: no minute limit).

The project has .github/workflows/dc.yml (`dc init` writes it), which calls this repo's
reusable run.yml. `dc run` dispatches it on the pushed commit, waits and downloads the
artifacts. Inputs and outputs that must not be public (artifacts of a public repo can be
downloaded by any GitHub account) go through Drive (--in, --out) and never into artifacts
or logs. Every command of --each is a job of its own, in parallel.
"""
from __future__ import annotations

import calendar
import json
import os
import re
import shlex
import subprocess
import sys
import time
import uuid

from . import core, drive, secrets
from .core import Fail, say

WORKFLOW = "dc.yml"
TEMPLATE = os.path.join(core.ROOT, "templates", WORKFLOW)
TAIL = 15


def init(p: core.Project) -> None:
    dst = os.path.join(p.root, ".github", "workflows", WORKFLOW)
    g = lambda *a: core.out(core.run(["git", "-C", core.ROOT, *a], check=False))
    ref = g("rev-parse", "HEAD")
    if not ref or not g("branch", "-r", "--contains", ref):
        raise Fail("devcloud's own commit is not pushed: the workflow is pinned to it, push devcloud first")
    data = core.read(TEMPLATE).replace(b"OWNER", core.owner().encode()).replace(b"REF", ref.encode())
    if core.file_sha(dst) == core.sha(data):
        say(f"{dst}: up to date")
        return
    core.write(dst, data)
    say(f"wrote {dst}: commit and push it, then `dc secrets gh` gives it the secrets")


def _branch(p: core.Project) -> str:
    def g(*a):
        return core.out(core.run(["git", "-C", p.root, *a], check=False))
    branch = g("rev-parse", "--abbrev-ref", "HEAD")
    head, up = g("rev-parse", "HEAD"), g("rev-parse", "@{u}")
    if (not up or head != up) and os.environ.get("CLAUDE_CODE_REMOTE") and branch.startswith("claude/"):
        # a Claude Code web session's own branch: pushing it is how that session hands work over
        say(f"pushing {branch} (the runner uses the pushed commit)")
        core.run(["git", "-C", p.root, "push", "-q", "-u", "origin", branch])
        up = g("rev-parse", "@{u}")
    if not up or head != up:
        raise Fail("the runner uses the pushed commit: push first")
    if g("status", "--porcelain", "--untracked-files=no"):
        say("uncommitted changes are not part of the job")
    return branch


def default_branch(p: core.Project) -> str:
    # REST, not `gh repo view` (GraphQL, refused in a Claude Code web session)
    return core.out(core.gh("api", f"repos/{p.repo}", "-q", ".default_branch"))


def dispatch(p: core.Project, commands: list[str], runner: str, inputs: list[str], outputs: list[str],
             artifacts: list[str], wait: bool, ref: str | None = None) -> int:
    """ref: run on that branch as pushed (for jobs that bring everything they need through
    Drive); by default the current branch, which must be pushed."""
    if not p.repo:
        raise Fail(f"{p.name}: no GitHub origin")
    wf = os.path.join(p.root, ".github", "workflows", WORKFLOW)
    if not os.path.isfile(wf):
        raise Fail(f"no .github/workflows/{WORKFLOW} in {p.name}: dc init")
    branch, tag = ref or _branch(p), uuid.uuid4().hex[:8]
    fields = {"id": tag, "commands": json.dumps(commands), "os": runner,
              "drive_in": "\n".join(inputs), "drive_out": "\n".join(outputs),
              "artifacts": "\n".join(artifacts)}
    if b"inputs.title" in core.read(wf):   # a dc.yml older than the short run name has no title input
        fields["title"] = title(commands)
    args = ["workflow", "run", WORKFLOW, "-R", p.repo, "--ref", branch]
    for k, v in fields.items():
        args += ["-f", f"{k}={v}"]
    core.gh(*args)
    run_id = None
    for _ in range(40):
        time.sleep(3)
        rows = json.loads(core.out(core.gh("run", "list", "-R", p.repo, "-w", WORKFLOW, "-L", "20",
                                           "--json", "databaseId,displayTitle,url")))
        hit = [r for r in rows if tag in r["displayTitle"]]
        if hit:
            run_id, url = hit[0]["databaseId"], hit[0]["url"]
            break
    if not run_id:
        raise Fail("dispatched, but the run did not show up")
    say(f"run {url}")
    if not wait:
        return 0
    rc = subprocess.call([core.tool("gh"), "run", "watch", str(run_id), "-R", p.repo, "--exit-status",
                          "--interval", "20"], env=core.gh_env(), stdout=subprocess.DEVNULL)
    tails(p.repo, run_id)
    if artifacts:
        dst = os.path.join(p.root, "dc-out", tag)
        core.gh("run", "download", str(run_id), "-R", p.repo, "-D", dst, check=False)
        say(f"artifacts in {dst}")
    if rc:
        say(f"run failed: {url}")
    elif core.gh("run", "delete", str(run_id), "-R", p.repo, check=False).returncode == 0:
        say("run succeeded (deleted from the Actions history; failed runs are kept)")
    else:
        say(f"run succeeded: {url}")
    return rc


def title(commands: list[str], width: int = 60) -> str:
    """The run's short name: the first command without its setup prefix (`a && b` -> b),
    cut to width, and how many more commands run beside it."""
    s = commands[0].split("&&")[-1].strip() if commands else ""
    if len(s) > width:
        s = s[:width - 3] + "..."
    return s + (f" (+{len(commands) - 1})" if len(commands) > 1 else "")


# ---------------------------------------------------------------- Actions history

KEEP = 6              # runs a repo shows at most: the newest success of each workflow
FAILED_DAYS = 1.0     # a failed run stays this long, to read its log


def prune(runs: list[dict], now: float, keep: int = KEEP) -> list[int]:
    """The runs to delete (API run objects): everything finished except the newest success of
    each workflow other than dc.yml (at most keep of them, newest first) and failures younger
    than FAILED_DAYS. Running and queued runs are left alone."""
    done = sorted((r for r in runs if r.get("status") == "completed"),
                  key=lambda r: r["created_at"], reverse=True)
    kept, seen = set(), set()
    for r in done:
        wf = r.get("path", "").split("@")[0]
        if r.get("conclusion") == "success" and not wf.endswith("/" + WORKFLOW) \
                and r["workflow_id"] not in seen and len(seen) < keep:
            seen.add(r["workflow_id"])
            kept.add(r["id"])
        elif r.get("conclusion") == "failure" and now - _epoch(r["created_at"]) < FAILED_DAYS * 86400:
            kept.add(r["id"])
    return [r["id"] for r in done if r["id"] not in kept]


def _epoch(stamp: str) -> float:
    return calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))


def gc_repos(every: bool) -> list[str]:
    """The current project's repo; with every, devcloud's and every registered project's."""
    p = core.project()
    repos = [p.repo] if p and p.repo else []
    if every:
        repos.append(f"{core.owner()}/devcloud")
        repos += [e["repo"] for e in core.registry().values() if e.get("repo")]
    return list(dict.fromkeys(repos))


def gc(repos: list[str], dry: bool) -> int:
    """Trims each repo's Actions history to what prune keeps (`dc gc`)."""
    total = 0
    for repo in repos:
        r = core.gh("api", "--paginate", f"repos/{repo}/actions/runs?per_page=100",
                    "-q", ".workflow_runs[]", check=False)
        if r.returncode:
            say(f"{repo}: runs not readable, skipped")
            continue
        runs = [json.loads(s) for s in core.out(r).splitlines() if s.strip()]
        ids = prune(runs, time.time())
        n = 0 if dry else sum(core.gh("api", "-X", "DELETE", f"repos/{repo}/actions/runs/{i}",
                                      check=False).returncode == 0 for i in ids)
        if ids:
            say(f"{repo}: {len(runs)} runs, {'would delete' if dry else 'deleted'} {len(ids) if dry else n}")
        total += n
    return total


_STAMP = re.compile(r"^\ufeff?\d{4}-\d\d-\d\dT[\d:.]+Z ?")
_ESC = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")


def log_tail(log: str, n: int = TAIL) -> list[str]:
    """The last n lines of the command's own output in a job's raw log (from `dc: $` to the
    next step); the whole log's tail when the job failed before the command."""
    lines = [_ESC.sub("", _STAMP.sub("", s)).replace("\x00", "") for s in log.splitlines()]
    start = max((i for i, s in enumerate(lines) if s.startswith("dc: $ ")), default=None)
    if start is None:
        return lines[-n:]
    body = []
    for s in lines[start + 1:]:
        if s.startswith("##[group]") or s.startswith("Post job cleanup"):
            break
        body.append(s)
    return body[-n:]


def tails(repo: str, run_id: int) -> None:
    """Each job's conclusion and the tail of its output, so the log needs no browser."""
    r = core.gh("api", f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100", check=False)
    if r.returncode:
        return
    for job in json.loads(core.out(r)).get("jobs", []):
        log = core.gh("api", "--allow-escape-sequences", f"repos/{repo}/actions/jobs/{job['id']}/logs",
                     check=False)
        print(f"--- {job['name']}: {job.get('conclusion')}", file=sys.stderr)
        for s in log_tail(core.out(log)) if log.returncode == 0 else ["(log not available)"]:
            print(f"  {s}", file=sys.stderr)
    sys.stderr.flush()


def _bash() -> str:
    """Git Bash on Windows: a bare "bash" resolves to System32's WSL launcher first."""
    if os.name == "nt":
        git = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Git", "bin", "bash.exe")
        if os.path.isfile(git):
            return git
    return "bash"


def ci() -> int:
    """Inside the Actions job: secrets into the environment (masked), Drive inputs, the
    command, Drive outputs."""
    env = secrets.parse(os.environ.get("DC_ENV", ""))
    # rclone's TYPE and SCOPE ("drive") are settings, not secrets: masked, every "drive" in the
    # log would turn into ***
    for k, v in env.items():
        if len(v) >= 4 and not k.endswith(("_TYPE", "_SCOPE")):
            for part in v.splitlines():
                print(f"::add-mask::{part}", flush=True)
    os.environ.update(env)
    core.setup_tools()                           # a fresh runner: age, gh, rclone
    p = core.need_project()
    dot = os.path.join(p.root, ".env")
    if env and not os.path.exists(dot):          # tools that read .env find their tokens
        core.write(dot, "".join(f"{k}={v}\n" for k, v in env.items()).encode(), private=True)
    for line in filter(None, os.environ.get("DC_IN", "").splitlines()):
        rel, _, local = line.strip().partition(" ")
        drive.get(p, rel, os.path.join(p.root, local.strip() or "."))
    cmd = os.environ["DC_COMMAND"]
    print(f"dc: $ {cmd}", flush=True)
    rc = subprocess.call([_bash(), "-c", cmd], cwd=p.root)
    for line in filter(None, os.environ.get("DC_OUT", "").splitlines()):
        local, _, rel = line.strip().partition(" ")
        src = os.path.join(p.root, local)
        if os.path.exists(src):
            drive.put(p, src, rel.strip() or local, [])
        else:
            say(f"{local}: not produced, not uploaded")
    return rc


def brief(p: core.Project, command: str, inputs: list[str], outputs: list[str]) -> str:
    """A self-contained task for a Claude Code web session (the fallback when Actions does not
    fit: more than 6 h, or it needs the session itself)."""
    head = core.out(core.run(["git", "-C", p.root, "rev-parse", "HEAD"]))
    lines = [
        f"Run a job for {p.repo} at commit {head}.",
        "1. Clone it, check out that commit, and clone devcloud next to it: "
        f"git clone https://github.com/{core.owner()}/devcloud ~/devcloud && ~/devcloud/dc setup "
        "(DC_AGE_KEY and DC_GH_TOKEN are in this environment's variables).",
        "2. In the project folder: ~/devcloud/dc secrets pull",
    ]
    for line in inputs:
        rel, _, local = line.partition(" ")
        lines.append(f"   ~/devcloud/dc drive get {shlex.quote(rel)} {shlex.quote(local or '.')}")
    lines.append(f"3. Run: {command}")
    for line in outputs:
        local, _, rel = line.partition(" ")
        lines.append(f"4. Upload: ~/devcloud/dc drive put {shlex.quote(local)} {shlex.quote(rel or local)}")
    lines.append("Never commit or print the inputs, outputs or secrets. Report the exit code and "
                 "the last lines of the output.")
    return "\n".join(lines)


def command_of(argv: list[str]) -> str:
    return argv[0] if len(argv) == 1 else shlex.join(argv)


if __name__ == "__main__":
    sys.exit(ci())
