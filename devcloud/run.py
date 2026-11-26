"""CPU jobs on GitHub Actions, in the project's own repo (public repos: no minute limit).

The project has .github/workflows/dc.yml (`dc init` writes it), which calls this repo's
reusable run.yml. `dc run` dispatches it on the pushed commit, waits and downloads the
artifacts. Inputs and outputs that must not be public (artifacts of a public repo can be
downloaded by any GitHub account) go through Drive (--in, --out) and never into artifacts
or logs. Every command of --each is a job of its own, in parallel.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
import uuid

from . import core, drive, secrets
from .core import Fail, say

WORKFLOW = "dc.yml"
TEMPLATE = os.path.join(core.ROOT, "templates", WORKFLOW)


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
    if not up or head != up:
        raise Fail("the runner uses the pushed commit: push first")
    if g("status", "--porcelain", "--untracked-files=no"):
        say("uncommitted changes are not part of the job")
    return branch


def dispatch(p: core.Project, commands: list[str], runner: str, inputs: list[str], outputs: list[str],
             artifacts: list[str], wait: bool) -> int:
    if not p.repo:
        raise Fail(f"{p.name}: no GitHub origin")
    if not os.path.isfile(os.path.join(p.root, ".github", "workflows", WORKFLOW)):
        raise Fail(f"no .github/workflows/{WORKFLOW} in {p.name}: dc init")
    branch, tag = _branch(p), uuid.uuid4().hex[:8]
    fields = {"id": tag, "commands": json.dumps(commands), "os": runner,
              "drive_in": "\n".join(inputs), "drive_out": "\n".join(outputs),
              "artifacts": "\n".join(artifacts)}
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
    if artifacts:
        dst = os.path.join(p.root, "dc-out", tag)
        core.gh("run", "download", str(run_id), "-R", p.repo, "-D", dst, check=False)
        say(f"artifacts in {dst}")
    say(f"run {'succeeded' if rc == 0 else 'failed'}: {url}")
    return rc


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
    rc = subprocess.call(["bash", "-c", cmd], cwd=p.root)
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
