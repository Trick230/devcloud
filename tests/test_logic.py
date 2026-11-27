"""The pure parts: three-way merge, .env split and parse, Drive paths, Claude slugs."""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from devcloud import account, claude, core, drive, gpu, run, secrets, session  # noqa: E402


class GitIdentity(unittest.TestCase):
    def test_identity(self):
        self.assertEqual(session.identity("Claude", "me", "7", {}), ("me", "7+me@users.noreply.github.com"))
        self.assertEqual(session.identity("", "me", "7", {"DC_GIT_EMAIL": "a@b.c"}), ("me", "a@b.c"))
        self.assertIsNone(session.identity("Someone", "me", "7", {}))   # already set: kept
        self.assertIsNone(session.identity("Claude", "", "", {}))       # no account: left alone


class GpuRelay(unittest.TestCase):
    def test_command(self):
        c = gpu.relay_command("gpu/job.json", ["--model", "a b"], True, "A10G")
        self.assertIn("pipx install -q modal==1.6.4", c)
        self.assertIn("--preinstall six kaggle==2.2.5", c)
        self.assertTrue(c.endswith("./.devcloud/dc gpu --long --gpu A10G gpu/job.json "
                                   "--in dc-gpu-in/* --out dc-gpu-out -- --model 'a b'"))

    def test_bundle(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            os.makedirs(os.path.join(t, "job"))
            os.makedirs(os.path.join(t, "pkg"))
            for f in ("job/run.py", "pkg/helper.py"):
                open(os.path.join(t, f), "w").close()
            with open(os.path.join(t, "job", "spec.json"), "w") as f:
                json.dump({"name": "x", "script": "run.py", "files": ["../pkg/helper.py"]}, f)
            d = os.path.join(t, "out")
            gpu.relay_bundle(gpu.spec_of(os.path.join(t, "job", "spec.json")), d)
            self.assertEqual(sorted(os.listdir(d)), ["helper.py", "job.json", "run.py"])
            s = gpu.spec_of(os.path.join(d, "job.json"))
            self.assertEqual((s["script"], s["files"]), ("run.py", ["helper.py"]))


class Merge(unittest.TestCase):
    def test_decide(self):
        d = core.decide
        self.assertEqual(d("a", "a", "a"), "same")
        self.assertEqual(d("b", "a", "a"), "take_local")      # changed here
        self.assertEqual(d("a", "a", "b"), "take_state")      # changed elsewhere
        self.assertEqual(d(None, None, "b"), "take_state")    # new elsewhere
        self.assertEqual(d("b", None, None), "take_local")    # new here
        self.assertEqual(d("b", "a", "c"), "conflict")
        self.assertEqual(d("c", "a", "c"), "same")
        self.assertEqual(d(None, "a", "a"), "same")           # deleted here: not propagated


class Env(unittest.TestCase):
    def test_split(self):
        s, l = secrets.split("A=1\nB=2\n# dc: local\nP=/x\n")
        self.assertEqual(s, "A=1\nB=2\n")
        self.assertEqual(l, "# dc: local\nP=/x\n")
        self.assertEqual(secrets.split("A=1\n"), ("A=1\n", ""))

    def test_parse(self):
        e = secrets.parse('# c\nA=1\n B = "two words" \nC=\'x=y\'\nbad\n')
        self.assertEqual(e, {"A": "1", "B": "two words", "C": "x=y"})

    def test_for_github(self):
        g = {drive.PREFIX + "TOKEN": "t", "MODAL_TOKEN_ID": "m", "KAGGLE_USERNAME": "k"}
        self.assertEqual(secrets.for_github(g, {}), {})                      # nothing by default
        self.assertEqual(secrets.for_github(g, {"X": "1"}), {"X": "1"})
        e = secrets.for_github(g, {"DC_GLOBAL": "drive, modal", "X": "1"})
        self.assertEqual(e, {drive.PREFIX + "TOKEN": "t", "MODAL_TOKEN_ID": "m", "X": "1"})
        self.assertEqual(secrets.for_github(g, {"DC_GLOBAL": "kaggle", "KAGGLE_USERNAME": "own"}),
                         {"KAGGLE_USERNAME": "own"})                        # the project's wins


class Drive(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(drive.clean("a/./b//c/"), "a/b/c")
        self.assertEqual(drive.clean(r"a\b"), "a/b")
        for bad in ("../x", "a/../../x", "C:/x"):
            with self.assertRaises(core.Fail):
                drive.clean(bad)

    def test_authorize_token(self):
        import base64
        import json
        tok = {"access_token": "a", "token_type": "Bearer", "refresh_token": "r"}
        plain = f"Paste the following --->\n{json.dumps(tok)}\n<---End paste"
        self.assertEqual(drive._authorize_token(plain), tok)
        blob = base64.urlsafe_b64encode(json.dumps({"token": json.dumps(tok)}).encode()).decode().rstrip("=")
        self.assertEqual(drive._authorize_token(f"--->\n{blob}\n<---End paste"), tok)
        self.assertIsNone(drive._authorize_token("--->\nnot-a-token\n<---"))
        self.assertIsNone(drive._authorize_token("nothing"))

    def test_walk_skips_private(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            for rel in (".env", ".env.local", "id.pem", "a/keep.txt", ".git/config",
                        ".lls/x/bin", "a/node_modules/m.js", "b/.env"):
                f = os.path.join(d, *rel.split("/"))
                os.makedirs(os.path.dirname(f), exist_ok=True)
                open(f, "w").close()
            got = sorted(os.path.relpath(f, d).replace(os.sep, "/") for f in drive.walk(d))
        self.assertEqual(got, ["a/keep.txt"])

    def test_remote(self):
        p = core.Project("/r", "Proj", "o/Proj")
        self.assertEqual(drive.remote(p), "DCDRIVE:Projects/Proj")
        self.assertEqual(drive.remote(p, "refs/a.flac"), "DCDRIVE:Projects/Proj/refs/a.flac")


class Claude(unittest.TestCase):
    def test_slug(self):
        if os.name == "nt":
            self.assertEqual(claude.slug(r"D:\Non\LosslessSync"), "D--Non-LosslessSync")
        else:
            self.assertEqual(claude.slug("/home/u/my.proj"), "-home-u-my-proj")

    def test_skip(self):
        for bad in ("claude/global/claude/tools/__pycache__/x.pyc", "claude/projects/P/.claude/settings.local.json",
                    "claude/x/CLAUDE.md.dc-remote"):
            self.assertTrue(claude.SKIP.search(bad), bad)
        self.assertFalse(claude.SKIP.search("claude/projects/P/.claude/settings.json"))

    def test_runs_code(self):
        for rel in ("claude/global/claude/settings.json", "claude/global/claude/tools/dc-hook.sh",
                    "claude/projects/P/.claude/hooks/x.py"):
            self.assertTrue(claude.runs_code(rel), rel)
        self.assertFalse(claude.runs_code("claude/global/claude/CLAUDE.md"))
        self.assertFalse(claude.runs_code("claude/projects/P/memory/state.md"))


class Gpu(unittest.TestCase):
    def test_job_status(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "s.json")
            self.assertEqual(gpu.job_status(f), 0)          # no file: provider trouble, not the job
            for body, rc in (('{"rc": 3}', 3), ('{"rc": 0}', 0), ("junk", 0), ("[]", 0)):
                with open(f, "w") as fh:
                    fh.write(body)
                self.assertEqual(gpu.job_status(f), rc, body)


class Write(unittest.TestCase):
    def test_reader_keeps_old_content(self):
        # a running hook script is read while it runs: a pull that rewrites it must not change
        # what the open handle reads (in-place truncation broke dc-hook.sh at SessionStart)
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "hook.sh")
            core.write(p, b"old " * 4096)
            if os.name != "nt":
                os.chmod(p, 0o755)
            with open(p, "rb") as f:
                f.read(10)
                core.write(p, b"new")
                if os.name != "nt":   # Windows refuses the swap while the file is open
                    self.assertEqual(f.read(6), b"d old ")
            self.assertEqual(core.read(p), b"new")
            self.assertEqual(os.listdir(d), ["hook.sh"])
            if os.name != "nt":
                self.assertEqual(os.stat(p).st_mode & 0o777, 0o755)


class RunLog(unittest.TestCase):
    def test_tail(self):
        log = ("﻿2026-01-01T00:00:00.1Z ##[group]Run x\n"
               "2026-01-01T00:00:00.2Z dc: $ make\n"
               + "".join(f"2026-01-01T00:00:01.0Z \x1b[32mline {i}\x1b[0m\n" for i in range(20))
               + "2026-01-01T00:00:02.0Z ##[group]Run upload\n"
               "2026-01-01T00:00:03.0Z Post job cleanup.\n")
        self.assertEqual(run.log_tail(log, 3), ["line 17", "line 18", "line 19"])
        self.assertEqual(run.log_tail("a\nb\nc", 2), ["b", "c"])   # failed before the command

    def test_title(self):
        self.assertEqual(run.title(["./lls setup --cloud && ./lls test pal"]), "./lls test pal")
        self.assertEqual(run.title(["a && b", "a && c", "a && d"]), "b (+2)")
        self.assertEqual(run.title(["x" * 80], 10), "xxxxxxx...")

    def test_prune(self):
        def r(i, wf, concl, hours_ago, status="completed"):
            t = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - hours_ago * 3600))
            return {"id": i, "workflow_id": wf, "path": f".github/workflows/w{wf}.yml",
                    "status": status, "conclusion": concl, "created_at": t}
        now = 1_800_000_000
        runs = [r(1, 1, "success", 1), r(2, 1, "success", 2),           # newest success of w1 stays
                r(3, 2, "failure", 2), r(4, 2, "failure", 30),          # failures: a day
                r(5, 3, "cancelled", 1), r(6, 3, None, 0, "in_progress"),
                {**r(7, 99, "success", 1), "path": ".github/workflows/dc.yml"}]  # dc runs never stay
        runs += [r(10 + w, w, "success", 3) for w in range(4, 10)]        # 6 more workflows: cap
        gone = run.prune(runs, now, keep=6)
        self.assertEqual(sorted(gone), [2, 4, 5, 7, 19])


class Account(unittest.TestCase):
    def test_switch(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as h:
            env = {"DC_HOME": h, "DC_ACCOUNTS": os.path.join(h, "slots")}
            old = {k: os.environ.get(k) for k in [*env, "CLAUDE_CONFIG_DIR"]}
            os.environ.update(env)
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
            try:
                cfg, cred = os.path.join(h, ".claude.json"), os.path.join(h, ".claude", ".credentials.json")
                def login(uuid, tok):
                    c = json.loads(core.read(cfg)) if os.path.exists(cfg) else {"projects": {"x": 1}}
                    c["oauthAccount"] = {"accountUuid": uuid, "emailAddress": uuid + "@x"}
                    core.write(cfg, json.dumps(c).encode())
                    core.write(cred, tok.encode())
                login("a", "tokA")
                account.save("a")
                login("b", "tokB")
                account.save("b")
                self.assertEqual(account.active_name(), "b")
                self.assertEqual(account.after("b"), "a")
                core.write(cred, b"tokB2")            # b's token rotated while active
                account.use("a")
                self.assertEqual(core.read(cred), b"tokA")
                self.assertEqual(account.active_name(), "a")
                self.assertEqual(json.loads(core.read(cfg))["projects"], {"x": 1})
                account.use("b")
                self.assertEqual(core.read(cred), b"tokB2")
                with self.assertRaises(core.Fail):
                    account.save("c")                 # b is already saved as b
            finally:
                for k, v in old.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v


class Token(unittest.TestCase):
    def test_warning(self):
        day, now = 86400, 1_800_000_000
        self.assertIsNone(core.token_warning(None, now))              # never expires / unknown
        self.assertIsNone(core.token_warning(now + 15 * day, now))
        self.assertIn("in 14 day(s)", core.token_warning(now + 14 * day, now))
        self.assertIn("in 0 day(s)", core.token_warning(now + 3600, now))
        self.assertIn("expired", core.token_warning(now - 3600, now))


class Session(unittest.TestCase):
    def test_other_active(self):
        c = {"session": "a", "where": "web", "host": "h", "time": 1000}
        self.assertIsNone(session.other_active(None, "b", 1000))
        self.assertIsNone(session.other_active(c, "a", 1100))              # our own claim
        self.assertIn("web on h", session.other_active(c, "b", 1000 + 600))
        self.assertIsNone(session.other_active(c, "b", 1000 + session.ACTIVE))   # expired
        self.assertIsNone(session.other_active(c, "b", 1100, previous="a"))      # it handed off to us

    def test_alive(self):
        import subprocess
        self.assertTrue(core._alive(str(os.getpid()).encode()))
        p = subprocess.Popen([sys.executable, "-c", "0"])
        p.wait()
        self.assertFalse(core._alive(str(p.pid).encode()))
        self.assertTrue(core._alive(b"junk"))

    def test_branches(self):
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            sh = lambda *a, cwd=d: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a],
                                                  cwd=cwd, check=True, capture_output=True)
            sh("init", "-q", "--bare", "-b", "master", "o.git")
            sh("clone", "-q", "o.git", "web")
            w = os.path.join(d, "web")
            commit = lambda cwd, f: (__import__("pathlib").Path(cwd, f).write_text(f), sh("add", f, cwd=cwd),
                                     sh("commit", "-q", "-m", f, cwd=cwd))
            commit(w, "base")
            sh("push", "-q", "origin", "HEAD:master", cwd=w)
            sh("clone", "-q", "o.git", "pc")
            pc = os.path.join(d, "pc")
            commit(w, "web1")
            sh("push", "-q", "origin", "HEAD:claude/a", cwd=w)
            msgs = session.branches(pc)
            self.assertEqual(len(msgs), 1)
            self.assertIn("fast-forwarded to origin/claude/a", msgs[0])
            self.assertTrue(os.path.exists(os.path.join(pc, "web1")))
            self.assertEqual(session.branches(pc), [])                      # nothing new
            commit(pc, "pc1")
            commit(w, "web2")
            sh("push", "-q", "origin", "HEAD:claude/a", cwd=w)
            msgs = session.branches(pc)
            self.assertEqual(len(msgs), 1)
            self.assertIn("diverged", msgs[0])
            self.assertIn(" web2", msgs[0])                                   # the commits it would lose
            self.assertNotIn("web1", msgs[0])                                 # already in the PC's branch
            self.assertIn("git push origin --delete claude/a", msgs[0])
            self.assertFalse(os.path.exists(os.path.join(pc, "web2")))
            sh("cherry-pick", "origin/claude/a", cwd=pc)                # integrated by hand
            self.assertEqual(session.branches(pc), [])                      # not pushed yet: stays
            sh("push", "-q", "origin", "master", cwd=pc)
            msgs = session.branches(pc)                                     # pushed: deleted
            self.assertEqual(len(msgs), 1)
            self.assertIn("deleted the remote branch claude/a", msgs[0])
            sh("fetch", "-q", cwd=w)
            sh("checkout", "-q", "-B", "t", "origin/master", cwd=w)
            commit(w, "tmp")
            sh("rm", "-q", "tmp", cwd=w)
            sh("commit", "-q", "-m", "undo tmp", cwd=w)
            sh("push", "-q", "origin", "HEAD:claude/b", cwd=w)               # no net change: deleted
            commit(w, "web3")
            sh("push", "-q", "origin", "HEAD:claude/c", cwd=w)               # new work: stays
            msgs = session.branches(pc)
            self.assertTrue(any("deleted the remote branch claude/b" in m for m in msgs), msgs)
            self.assertFalse(any("claude/c" in m and "deleted" in m for m in msgs), msgs)
            refs = sh("ls-remote", "--heads", "origin", cwd=pc).stdout.decode()
            self.assertNotIn("claude/b", refs)
            self.assertIn("claude/c", refs)


if __name__ == "__main__":
    unittest.main()
