"""Google Drive for files that must not go into git (copyrighted media, big data).

Each project has its folder, <Drive>/Projects/<project>/, through rclone; the remote (DCDRIVE) comes
from RCLONE_CONFIG_DCDRIVE_* in the global secrets, so no rclone.conf exists anywhere.
`push`/`pull` keep project files in sync with a manifest (drive/<project>.json in the state
repo: path, size, sha256); `ls`/`get`/`put` work on any path inside the project folder.
"""
from __future__ import annotations

import base64
import json
import os
import posixpath
import re
import subprocess
from functools import cache

from . import core, secrets
from .core import Fail, say

REMOTE = "DCDRIVE"
ROOT = "Projects"  # project folders sit in <Drive>/Projects/
PREFIX = f"RCLONE_CONFIG_{REMOTE}_"


def clean(rel: str) -> str:
    """A path inside the project folder, or Fail."""
    rel = rel.replace("\\", "/").strip("/")
    parts = [x for x in rel.split("/") if x not in ("", ".")]
    if any(x == ".." for x in parts) or re.match(r"^[A-Za-z]:", rel):
        raise Fail(f"{rel}: must be a path inside the project's Drive folder")
    return "/".join(parts)


def remote(p: core.Project, rel: str = "") -> str:
    return f"{REMOTE}:{posixpath.join(ROOT, p.name, clean(rel)).rstrip('/')}"


@cache
def _drive_secrets() -> dict:
    # one age decrypt per process, not one per rclone call
    return {k: v for k, v in secrets.env_for(None).items() if k.startswith(PREFIX)}


def rclone_env() -> dict:
    env = dict(os.environ)
    if not any(k.startswith(PREFIX) for k in env):
        env.update(_drive_secrets())
    if not any(k.startswith(PREFIX) for k in env):
        raise Fail("no Drive remote yet: dc drive login")
    env.setdefault("RCLONE_CONFIG", os.path.join(core.DC, "rclone-unused.conf"))
    return env


def rclone(*args: str, capture: bool = False, check: bool = True) -> str:
    r = subprocess.run([core.tool("rclone"), *args], env=rclone_env(), stdin=subprocess.DEVNULL,
                       capture_output=capture)
    if check and r.returncode != 0:
        err = (r.stderr or b"").decode(errors="replace")[-800:] if capture else ""
        raise Fail(f"rclone {args[0]} failed ({r.returncode}) {err}")
    return (r.stdout or b"").decode(errors="replace")


SCOPE = "drive.file"  # only the files dc itself created: a leaked token cannot reach the rest of the Drive


def login(client: list[str] | None = None) -> None:
    """Google sign-in in the browser; the token goes into the global secrets. With CLIENT_ID
    CLIENT_SECRET (an OAuth client of type "Desktop app" in Google Cloud), rclone uses that client
    instead of its shared one, which Google is retiring; without them, the stored client if any.
    Scope drive.file: dc sees only what it uploaded itself (files from other apps stay invisible)."""
    if client and len(client) != 2:
        raise Fail("dc drive login [CLIENT_ID CLIENT_SECRET]")
    if not client:
        g = _drive_secrets()
        if g.get(PREFIX + "CLIENT_ID") and g.get(PREFIX + "CLIENT_SECRET"):
            client = [g[PREFIX + "CLIENT_ID"], g[PREFIX + "CLIENT_SECRET"]]
            say("using the stored OAuth client")
    blob = {"scope": SCOPE, **({"client_id": client[0], "client_secret": client[1]} if client else {})}
    b64 = base64.urlsafe_b64encode(json.dumps(blob).encode()).decode().rstrip("=")
    r = subprocess.run([core.tool("rclone"), "authorize", "drive", b64], capture_output=True,
                       env=dict(os.environ, RCLONE_CONFIG=os.path.join(core.DC, "rclone-unused.conf")))
    text = r.stdout.decode(errors="replace")
    m = re.search(r"--->\s*(\{.*?\})\s*<---", text, re.S)
    if r.returncode != 0 or not m:
        raise Fail("rclone authorize did not return a token")
    token = json.dumps(json.loads(m.group(1)), separators=(",", ":"))
    keys = {PREFIX + "TYPE": "drive", PREFIX + "SCOPE": SCOPE, PREFIX + "TOKEN": token}
    # the token belongs to the client that issued it
    keys.update({PREFIX + "CLIENT_ID": client[0] if client else None,
                 PREFIX + "CLIENT_SECRET": client[1] if client else None})
    secrets.set_global(keys)
    say("Drive remote stored in the global secrets")


# ---------------------------------------------------------------- manifest

def _manifest_path(p: core.Project) -> str:
    return os.path.join(core.STATE, "drive", f"{p.name}.json")


def manifest(p: core.Project) -> dict:
    try:
        return json.loads(core.read(_manifest_path(p)))
    except (OSError, ValueError):
        return {}


def _save(p: core.Project, m: dict) -> None:
    core.write(_manifest_path(p), json.dumps(m, indent=1, sort_keys=True).encode())
    core.commit_state(f"drive {p.name}", [os.path.relpath(_manifest_path(p), core.STATE)])


def _rel(p: core.Project, path: str) -> str:
    full = os.path.abspath(path)
    rel = os.path.relpath(full, p.root)
    if rel.startswith(".."):
        raise Fail(f"{path}: not inside {p.root}")
    return clean(rel)


def push(p: core.Project, paths: list[str]) -> None:
    files = []
    for path in paths:
        if os.path.isdir(path):
            files += [os.path.join(d, f) for d, _, fs in os.walk(path) for f in fs]
        elif os.path.isfile(path):
            files.append(path)
        else:
            raise Fail(f"{path}: no such file")
    m, done = manifest(p), 0
    try:
        for f in sorted(files):
            rel, digest = _rel(p, f), core.file_sha(f)
            if m.get(rel, {}).get("sha256") == digest:
                continue
            say(f"up {rel}")
            rclone("copyto", f, remote(p, rel))
            m[rel] = {"sha256": digest, "size": os.path.getsize(f)}
            done += 1
    finally:
        if done:  # an interrupted push keeps what it uploaded
            _save(p, m)


def pull(p: core.Project, paths: list[str]) -> None:
    m = manifest(p)
    want = [_rel(p, x) for x in paths] if paths else list(m)
    for rel in sorted(want):
        sel = {k: v for k, v in m.items() if k == rel or k.startswith(rel + "/")}
        if not sel:
            raise Fail(f"{rel}: not in the Drive manifest of {p.name}")
        for k, e in sorted(sel.items()):
            local = os.path.join(p.root, *k.split("/"))
            if core.file_sha(local) == e["sha256"]:
                continue
            say(f"down {k}")
            rclone("copyto", remote(p, k), local)
            if core.file_sha(local) != e["sha256"]:
                raise Fail(f"{k}: sha256 differs from the manifest")


def forget(p: core.Project, paths: list[str], delete: bool) -> None:
    m = manifest(p)
    for x in paths:
        rel = clean(x)
        for k in [k for k in m if k == rel or k.startswith(rel + "/")]:
            if delete:
                rclone("deletefile", remote(p, k))
            del m[k]
    _save(p, m)


# ---------------------------------------------------------------- free paths

def ls(p: core.Project, rel: str, kind: str | None) -> list[str]:
    args = ["lsf", remote(p, rel)] + ({"dirs": ["--dirs-only"], "files": ["--files-only"]}[kind] if kind else [])
    return [x for x in rclone(*args, capture=True).splitlines() if x]


def get(p: core.Project, rel: str, local_dir: str) -> None:
    rclone("copy", remote(p, rel), local_dir)


def put(p: core.Project, local: str, rel: str, include: list[str]) -> None:
    args = ["copy", local, remote(p, rel)]
    for pat in include:
        args += ["--include", pat]
    rclone(*args)
