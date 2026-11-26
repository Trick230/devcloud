"""The pure parts: three-way merge, .env split and parse, Drive paths, Claude slugs."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from devcloud import claude, core, drive, secrets  # noqa: E402


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


class Drive(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(drive.clean("a/./b//c/"), "a/b/c")
        self.assertEqual(drive.clean(r"a\b"), "a/b")
        for bad in ("../x", "a/../../x", "C:/x"):
            with self.assertRaises(core.Fail):
                drive.clean(bad)

    def test_remote(self):
        p = core.Project("/r", "Proj", "o/Proj")
        self.assertEqual(drive.remote(p), "DCDRIVE:Proj")
        self.assertEqual(drive.remote(p, "refs/a.flac"), "DCDRIVE:Proj/refs/a.flac")


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


if __name__ == "__main__":
    unittest.main()
