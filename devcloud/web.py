"""What a Claude Code web environment needs, printed as plain lines to paste into claude.ai
(one environment per Claude account: switching accounts means creating it once more there).

The --prep lines (projects pre-built into the environment's image) live in the state repo,
web/prep.txt, one OWNER/REPO=CMD per line, so every machine prints the same environment.
"""
from __future__ import annotations

import os
import shlex

from . import core

ALLOW = ["github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com",
         "pypi.org", "files.pythonhosted.org", "download.pytorch.org", "static.rust-lang.org",
         "crates.io", "static.crates.io", "www.kaggle.com", "kaggle.com", "modal.com",
         "api.modal.com", "www.googleapis.com", "oauth2.googleapis.com", "*.googleusercontent.com"]
PREP = "web/prep.txt"


def preps() -> list[str]:
    try:
        text = core.read(os.path.join(core.STATE, PREP)).decode()
    except OSError:
        return []
    return [s.strip() for s in text.splitlines() if s.strip() and not s.startswith("#")]


def env_text() -> str:
    core.ensure_state()
    o = core.owner()
    sha = core.head_sha(core.ROOT)
    pushed = core.git("merge-base", "--is-ancestor", "HEAD", "origin/main", cwd=core.ROOT, check=False)
    if not sha or pushed.returncode != 0:
        raise core.Fail("push devcloud first: the setup script pins this checkout's commit")
    core.vouch_self()
    setup = " ".join(["~/devcloud/dc setup --web", *(f"--prep {shlex.quote(p)}" for p in preps())])
    # the secret line alone: the key file's comment lines would not fit a one-line variable
    key = next((s.strip() for s in core.read(core.KEY).decode().splitlines()
                if s.startswith("AGE-SECRET-KEY-")), "")
    def token(path: str, what: str) -> str:
        try:
            return core.read(path).decode().strip() or what
        except OSError:
            return what
    tok = token(core.WEB_TOKEN, f"(put it in {core.WEB_TOKEN}: fine-grained, the project repos and "
                "devcloud; Contents read, Actions and Secrets read/write)")
    stok = token(core.STATE_TOKEN, f"(put it in {core.STATE_TOKEN}: fine-grained, only "
                 f"{core.state_repo()}; Contents read/write)")
    lines = [
        "claude.ai > Claude Code > environments > new (name: devcloud). Connect GitHub to this account first.",
        "",
        "Environment variables:",
        f"DC_AGE_KEY={key}",
        f"DC_GH_TOKEN={tok}",
        f"DC_GH_STATE_TOKEN={stok}",
        "",
        "Setup script:",
        "#!/bin/sh",
        # the image runs this commit, not whatever the devcloud repo holds (the GitHub token alone
        # can push there); sessions then move on only to commits a PC vouched for (web-boot.sh)
        f"git clone -q https://github.com/{o}/devcloud ~/devcloud 2>/dev/null; "
        f"git -C ~/devcloud fetch -q origin main && git -C ~/devcloud checkout -q --detach {sha} || exit 1",
        setup,
        "",
        "Network access: custom, allowed domains:",
        *ALLOW,
        "",
        f"Start every session with the project AND {o}/devcloud-state selected.",
    ]
    return "\n".join(lines) + "\n"
