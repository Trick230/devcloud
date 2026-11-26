"""devcloud: secrets, Google Drive, the Claude setup, CPU and GPU jobs, the same on every machine.

  dc setup                  tools (age, gh, rclone) into .dc/, GitHub sign-in, the state repo,
                            the age key (the first machine creates it)
  dc setup --web [--prep OWNER/REPO=CMD]...
                            Claude Code web setup script: tools + hooks (web-boot.sh) that finish
                            the setup at SessionStart, where the environment variables exist;
                            --prep clones a public repo to ~/dc-prep/REPO and runs CMD there, so
                            the environment's cached image keeps what it downloads (the session's
                            .claude/web-setup.sh finds it in $DC_PREP)
  dc check                  what is set up here
  dc test                   unit tests
  dc key                    prints the age key (to copy to another machine or a cloud
                            environment's DC_AGE_KEY)

  dc secrets pull|push      the project's .env <-> the state repo (lines after `# dc: local`
                            stay here); --force on a conflict
  dc secrets gh             the global + project secrets as the Actions secret DC_ENV
  dc secrets set [--global] KEY=VALUE...   /  dc secrets unset --global KEY...

  dc drive login [ID SECRET] Google sign-in (browser), own OAuth client optional; global secrets
  dc drive push|pull [PATH...]   project files <-> <Drive>/Projects/<project>/, checked by sha256
  dc drive forget [--delete] PATH...
  dc drive ls [--dirs|--files] [REMOTE]  /  get REMOTE [LOCAL_DIR]  /  put LOCAL REMOTE [--include P]

  dc claude pull|push [--quiet]   ~/.claude, memory and the projects' agent files
  dc account [save NAME | use NAME | next]
                            Claude Code accounts on this machine: save the signed-in one, switch
                            to another without the browser (settings, memory, sessions shared)
  dc web-env                what to paste into claude.ai for a web environment (per account)
  dc report push FILE       a web session's boot report into the state repo (dc-hook.sh)
  dc report [show]          the project's last web boot report, read on another machine

  dc init                   writes the project's .github/workflows/dc.yml
  dc run [--os RUNNER] [--in REMOTE[:LOCAL]] [--out LOCAL[:REMOTE]] [--artifact PATH]
         [--no-wait] -- COMMAND...      one job on GitHub Actions (the pushed commit)
  dc run --each [...] "CMD1" "CMD2"...  one job per command, in parallel
  dc brief [--in ..] [--out ..] -- COMMAND...   the same job as a task for a Claude Code
                            web session (when Actions does not fit)

  dc gpu [--long] [--gpu TYPE] JOB.json --in FILE... --out DIR [-- ARGS...]
                            one GPU job on Modal, then Kaggle, then Colab (manual); JOB.json
                            and the script's contract: devcloud/gpu.py
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from devcloud import account, claude, core, drive, gpu, run, secrets, web  # noqa: E402
from devcloud.core import Fail, say  # noqa: E402


def web_hooks() -> None:
    """Points ~/.claude/settings.json's SessionStart/Stop/SessionEnd at web-boot.sh, keeping
    everything else in it (a web sandbox's setup script: no token yet, see web-boot.sh)."""
    p = os.path.join(core.home(), ".claude", "settings.json")
    try:
        s = json.loads(core.read(p))
    except (OSError, ValueError):
        s = {}
    boot = os.path.join(core.ROOT, "web-boot.sh")
    hooks = s.setdefault("hooks", {})
    for event, arg in (("SessionStart", "start"), ("Stop", "stop"), ("SessionEnd", "end")):
        keep = [g for g in hooks.get(event, []) if "web-boot.sh" not in json.dumps(g)]
        hooks[event] = keep + [{"hooks": [{"type": "command", "command": f'sh "{boot}" {arg}'}]}]
    core.write(p, (json.dumps(s, indent=2) + "\n").encode())
    say(f"web hooks in {p}")


def prep(specs: list[str]) -> None:
    """Each OWNER/REPO=CMD: clone (or update) the public repo into ~/dc-prep/REPO and run CMD in
    it. A failure is reported, not fatal: the session's own setup still runs."""
    for spec in specs:
        repo, _, cmd = spec.partition("=")
        d = os.path.join(os.path.expanduser("~"), "dc-prep", repo.rsplit("/", 1)[-1])
        if os.path.isdir(os.path.join(d, ".git")):
            r = core.run(["git", "-C", d, "pull", "-q", "--ff-only"], check=False)
        else:
            r = core.run(["git", "clone", "-q", f"https://github.com/{repo}", d], check=False)
        rc = r.returncode or (subprocess.call(["sh", "-c", cmd], cwd=d, stdin=subprocess.DEVNULL) if cmd else 0)
        say(f"prep {repo}: {'ok' if rc == 0 else f'failed ({rc})'}")


def setup(ci: bool, web: bool = False, preps: list[str] | None = None) -> int:
    core.setup_tools()
    if web:
        web_hooks()
        prep(preps or [])
        return 0
    if ci:
        return 0
    if not core.gh_token():
        if not sys.stdin.isatty():
            # the browser sign-in would wait forever in a sandbox setup script or a job
            raise Fail("no GitHub token: set DC_GH_TOKEN (in a web sandbox: the environment's variables)")
        core.login()
    core.ensure_state()
    core.ensure_key()
    p = core.project()
    if p:
        core.register(p)
    return check()


def check() -> int:
    ok = lambda b: "yes" if b else "no"  # noqa: E731
    print(f"platform : {core.PLAT}{' (ephemeral)' if core.EPHEMERAL else ''}")
    print(f"tools    : " + ", ".join(f"{t}: {ok(os.path.isfile(core.tool(t)))}" for t in ("age", "gh", "rclone")))
    print(f"github   : token {ok(core.gh_token())}")
    st = os.path.isdir(os.path.join(core.STATE, ".git"))
    print(f"state    : {core.STATE if st else 'not cloned (dc setup)'}")
    print(f"age key  : {ok(os.path.isfile(core.KEY) or os.environ.get('DC_AGE_KEY'))}")
    if st and (os.path.isfile(core.KEY) or os.environ.get("DC_AGE_KEY")):
        core.ensure_key()
        g = secrets.parse(secrets.state_text(secrets.GLOBAL) or "")
        print(f"drive    : {ok(any(k.startswith(drive.PREFIX) for k in g))}")
    p = core.project()
    if p:
        has = os.path.isfile(os.path.join(p.root, ".github", "workflows", run.WORKFLOW))
        print(f"project  : {p.name} ({p.repo or 'no GitHub origin'}) at {p.root}; dc.yml: {ok(has)}")
    return 0


def _io(items: list[str]) -> list[str]:
    """--in REMOTE[:LOCAL] / --out LOCAL[:REMOTE] (paths relative to the project and its
    Drive folder) as 'a b' lines."""
    res = []
    for it in items:
        a, _, b = it.partition(":")
        if " " in a or " " in b:
            raise Fail(f"{it}: no spaces in --in/--out paths")
        res.append(f"{a} {b}".strip())
    return res


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("help", "-h", "--help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    try:
        if cmd == "setup":
            preps = [rest[i + 1] for i, a in enumerate(rest[:-1]) if a == "--prep"]
            return setup("--ci" in rest, "--web" in rest, preps)
        if cmd == "check":
            return check()
        if cmd == "web-env":
            sys.stdout.write(web.env_text())
            return 0
        if cmd == "key":
            sys.stdout.write(core.read(core.KEY).decode())
            return 0
        if cmd == "ci":
            return run.ci()
        if cmd == "test":
            import unittest
            t = unittest.defaultTestLoader.discover(os.path.join(core.ROOT, "tests"))
            return 0 if unittest.TextTestRunner(verbosity=2).run(t).wasSuccessful() else 1

        ap = argparse.ArgumentParser(prog=f"dc {cmd}")
        if cmd == "account":
            ap.add_argument("action", nargs="?", default="list", choices=["list", "save", "use", "next"])
            ap.add_argument("name", nargs="?")
            a = ap.parse_args(rest)
            if a.action in ("save", "use") and not a.name:
                raise Fail(f"dc account {a.action} NAME")
            if a.action == "save":
                account.save(a.name)
            elif a.action == "use":
                account.use(a.name)
            elif a.action == "next":
                n = account.after(account.active_name())
                if n:
                    print(n)
            else:
                account.show()
            return 0
        if cmd == "secrets":
            ap.add_argument("action", choices=["pull", "push", "gh", "set", "unset"])
            ap.add_argument("--global", dest="glob", action="store_true")
            ap.add_argument("--force", action="store_true")
            ap.add_argument("items", nargs="*")
            a = ap.parse_args(rest)
            core.ensure_state()
            core.ensure_key()
            if a.action in ("set", "unset"):
                if not a.glob:
                    raise Fail("only the global env is set this way; edit the project's .env and push")
                secrets.set_global({k.split("=", 1)[0]: (k.split("=", 1)[1] if a.action == "set" else None)
                                    for k in a.items})
                return 0
            p = core.need_project()
            core.register(p)
            {"pull": lambda: secrets.pull(p, a.force), "push": lambda: secrets.push(p, a.force),
             "gh": lambda: secrets.to_github(p)}[a.action]()
            return 0
        if cmd == "drive":
            ap.add_argument("action", choices=["login", "push", "pull", "forget", "ls", "get", "put"])
            ap.add_argument("items", nargs="*")
            ap.add_argument("--dirs", action="store_true")
            ap.add_argument("--files", action="store_true")
            ap.add_argument("--delete", action="store_true")
            ap.add_argument("--include", action="append", default=[])
            a = ap.parse_args(rest)
            if not core.CI:
                core.ensure_state()
                core.ensure_key()
            if a.action == "login":
                drive.login(a.items)
                return 0
            p = core.need_project()
            if a.action == "push":
                drive.push(p, a.items or ["."])
            elif a.action == "pull":
                drive.pull(p, a.items)
            elif a.action == "forget":
                drive.forget(p, a.items, a.delete)
            elif a.action == "ls":
                print("\n".join(drive.ls(p, a.items[0] if a.items else "",
                                         "dirs" if a.dirs else "files" if a.files else None)))
            elif a.action == "get":
                drive.get(p, a.items[0], a.items[1] if len(a.items) > 1 else ".")
            else:
                drive.put(p, a.items[0], a.items[1], a.include)
            return 0
        if cmd == "claude":
            ap.add_argument("action", choices=["pull", "push"])
            ap.add_argument("--quiet", action="store_true")
            a = ap.parse_args(rest)
            (claude.pull if a.action == "pull" else claude.push)(a.quiet)
            return 0
        if cmd == "report":
            ap.add_argument("action", nargs="?", default="show", choices=["show", "push"])
            ap.add_argument("file", nargs="?")
            a = ap.parse_args(rest)
            core.ensure_state()
            rel = f"reports/{core.need_project().name}-web.txt"
            dst = os.path.join(core.STATE, rel)
            if a.action == "push":
                if not a.file:
                    raise Fail("dc report push FILE")
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                core.write(dst, core.read(a.file))
                core.commit_state(f"report: {rel}", [rel])
                return 0
            if not os.path.isfile(dst):
                raise Fail(f"no {rel} yet (a web session writes it at SessionStart)")
            sys.stdout.write(core.read(dst).decode(errors="replace"))
            return 0
        if cmd == "init":
            run.init(core.need_project())
            return 0
        if cmd == "gpu":
            ap.add_argument("job")
            ap.add_argument("--in", dest="inp", nargs="+", action="extend", default=[])
            ap.add_argument("--out", required=True)
            ap.add_argument("--long", action="store_true")
            ap.add_argument("--gpu")
            cut = rest.index("--") if "--" in rest else len(rest)
            a, args = ap.parse_args(rest[:cut]), rest[cut + 1:]
            if a.gpu:
                os.environ["DC_GPU_TYPE"] = a.gpu
            if not core.CI:
                core.ensure_state()
                core.ensure_key()
            return gpu.dispatch(core.project(), a.job, a.inp, a.out, args, a.long)
        if cmd in ("run", "brief"):
            ap.add_argument("--os", default="ubuntu-latest")
            ap.add_argument("--in", dest="inp", action="append", default=[])
            ap.add_argument("--out", action="append", default=[])
            ap.add_argument("--artifact", action="append", default=[])
            ap.add_argument("--each", action="store_true")
            ap.add_argument("--no-wait", action="store_true")
            ap.add_argument("command", nargs=argparse.REMAINDER)
            a = ap.parse_args(rest)
            argv_cmd = a.command[1:] if a.command[:1] == ["--"] else a.command
            if not argv_cmd:
                raise Fail("no command")
            commands = argv_cmd if a.each else [run.command_of(argv_cmd)]
            ins, outs = _io(a.inp), _io(a.out)
            p = core.need_project()
            if cmd == "brief":
                print("\n\n".join(run.brief(p, c, ins, outs) for c in commands))
                return 0
            return run.dispatch(p, commands, a.os, ins, outs, a.artifact, not a.no_wait)
        raise Fail(f"unknown command {cmd} (dc help)")
    except Fail as e:
        say(f"ERROR: {e}")
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
