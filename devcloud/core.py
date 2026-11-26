"""Paths, the pinned tools (age, gh, rclone), GitHub auth and the private state repo.

Everything lives in .dc/ next to this repo: .dc/<platform>/ (uv, Python, tools), .dc/state
(a clone of <owner>/devcloud-state), .dc/age-key.txt (the one secret), .dc/gh-token and
.dc/sync.json (what was last synced, per machine). Standard library only.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIN = os.name == "nt"
EXE = ".exe" if WIN else ""
OS = "windows" if WIN else "linux"


def platform_key() -> str:
    arch = platform.machine().lower()
    if WIN and "ARM64" in (os.environ.get("PROCESSOR_ARCHITECTURE", "") +
                           os.environ.get("PROCESSOR_ARCHITEW6432", "")).upper():
        arch = "arm64"
    return f"{OS}-{'arm64' if arch in ('arm64', 'aarch64') else 'x64'}"


PLAT = platform_key()
DC = os.path.join(ROOT, ".dc")
LOCAL = os.path.join(DC, PLAT)
BIN = os.path.join(LOCAL, "bin")
STATE = os.path.join(DC, "state")
KEY = os.path.join(DC, "age-key.txt")
TOKEN = os.path.join(DC, "gh-token")
SYNC = os.environ.get("DC_SYNC") or os.path.join(DC, "sync.json")   # DC_SYNC: tests
CI = bool(os.environ.get("GITHUB_ACTIONS"))
# a Claude Code web sandbox or a CI runner: its paths are not worth remembering
EPHEMERAL = CI or bool(os.environ.get("CLAUDE_CODE_REMOTE"))

GH = "https://github.com"
_AGE = f"{GH}/FiloSottile/age/releases/download/v1.3.2/age-v1.3.2"
_GH = f"{GH}/cli/cli/releases/download/v2.103.3/gh_2.103.3"
_RC = f"{GH}/rclone/rclone/releases/download/v1.76.0/rclone-v1.76.0"
TOOLS = {   # name: {platform: (url, sha256)}; each archive's members are found by base name
    "age": {
        "windows-x64": (f"{_AGE}-windows-amd64.zip", "f48d8f8f9ebe903ab5027ed067652f2cc1db94bc206976430133b905dcd8e8c7"),
        "windows-arm64": (f"{_AGE}-windows-arm64.zip", "fae351336c8d5f30f93cc3ccdec16d1ed7851d68cdd6656d41955cc31a447c9d"),
        "linux-x64": (f"{_AGE}-linux-amd64.tar.gz", "cbe24006683f8eb669266162894b9a522a1af52f2665fbc63a4bb032ed26ac10"),
        "linux-arm64": (f"{_AGE}-linux-arm64.tar.gz", "6b8dc4333c53a5a57c9e5834e3a48f92605d7154014cd07269ff3327db5d37f4"),
    },
    "gh": {
        "windows-x64": (f"{_GH}_windows_amd64.zip", "98d0cfa794721665d7973dd728c78aff7bd836d58f10f6d7a03568eccb0a22e0"),
        "windows-arm64": (f"{_GH}_windows_arm64.zip", "197211c98687d7770ac331fe8a3b6c198c389108c1536cdbedf659fe4703e484"),
        "linux-x64": (f"{_GH}_linux_amd64.tar.gz", "4c806978ffce1cec70853302a56d70ac6da4f19bae7adca4e1a8ab00633dd91d"),
        "linux-arm64": (f"{_GH}_linux_arm64.tar.gz", "ee32df2323c8847f5fac6cb6b4b69ca75828e9329a8f22f677021f7b3306ce63"),
    },
    "rclone": {
        "windows-x64": (f"{_RC}-windows-amd64.zip", "8b4fd2737ade4e70c057ef4a49deb7cffe94b0fb9d7587c189d149823f21a1dc"),
        "windows-arm64": (f"{_RC}-windows-arm64.zip", "c406c8a69a5a408e50624aa990fbb0a6266b3a4e83782a94de3cb96f50f86f10"),
        "linux-x64": (f"{_RC}-linux-amd64.zip", "b00f00b3266c3764413afe599d3ec316f6f43b3e0ea8a0bc1d186d2133e46df7"),
        "linux-arm64": (f"{_RC}-linux-arm64.zip", "ee169016675e23a69d77240565133ff7dbc9b33d16fa8f7053d4af39f1eaeb78"),
    },
}
MEMBERS = {"age": ["age", "age-keygen"], "gh": ["gh"], "rclone": ["rclone"]}


class Fail(Exception):
    pass


def say(msg: str) -> None:
    print(f"dc: {msg}", file=sys.stderr, flush=True)


def home() -> str:
    """The user's home; DC_HOME replaces it (tests)."""
    return os.environ.get("DC_HOME") or os.path.expanduser("~")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha(path: str) -> str | None:
    try:
        with open(path, "rb") as f:
            return sha(f.read())
    except OSError:
        return None


def read(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def write(path: str, data: bytes, private: bool = False) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    if private and not WIN:
        os.chmod(path, 0o600)


# ---------------------------------------------------------------- tools

def tool(name: str) -> str:
    return os.path.join(BIN, name + EXE)


def fetch(url: str, digest: str) -> bytes:
    cache = os.path.join(LOCAL, "cache", "downloads", os.path.basename(url))
    if file_sha(cache) == digest:
        return read(cache)
    say(f"downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "devcloud-setup"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
    if sha(data) != digest:
        raise Fail(f"{url}: sha256 mismatch")
    write(cache, data)
    return data


def extract(data: bytes, url: str, names: list[str]) -> None:
    os.makedirs(BIN, exist_ok=True)
    want = {n + EXE for n in names}
    found = set()
    if url.endswith(".zip"):
        z = zipfile.ZipFile(io.BytesIO(data))
        for m in z.namelist():
            if os.path.basename(m) in want:
                write(os.path.join(BIN, os.path.basename(m)), z.read(m))
                found.add(os.path.basename(m))
    else:
        with tarfile.open(fileobj=io.BytesIO(data)) as t:
            for m in t.getmembers():
                if m.isfile() and os.path.basename(m.name) in want:
                    write(os.path.join(BIN, os.path.basename(m.name)), t.extractfile(m).read())
                    found.add(os.path.basename(m.name))
    for n in found:
        if not WIN:
            os.chmod(os.path.join(BIN, n), 0o755)
    if found != want:
        raise Fail(f"{url}: missing {', '.join(sorted(want - found))}")


def setup_tools() -> None:
    for name, table in TOOLS.items():
        if all(os.path.isfile(tool(m)) for m in MEMBERS[name]):
            continue
        if PLAT not in table:
            raise Fail(f"no {name} for {PLAT}")
        url, digest = table[PLAT]
        extract(fetch(url, digest), url, MEMBERS[name])


def run(cmd: list[str], *, capture: bool = True, check: bool = True, input: bytes | None = None,
        env: dict | None = None, cwd: str | None = None) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, input=input, capture_output=capture, env=env, cwd=cwd,
                       stdin=None if input is not None else subprocess.DEVNULL)
    if check and r.returncode != 0:
        err = (r.stderr or b"").decode(errors="replace").strip() if capture else ""
        raise Fail(f"{os.path.basename(cmd[0])} {cmd[1] if len(cmd) > 1 else ''} failed "
                   f"({r.returncode}){': ' + err[-800:] if err else ''}")
    return r


def out(r: subprocess.CompletedProcess) -> str:
    return r.stdout.decode(errors="replace").strip()


# ---------------------------------------------------------------- GitHub

def gh_token() -> str | None:
    t = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if t:
        return t
    try:
        return read(TOKEN).decode().strip() or None
    except OSError:
        return None


def gh_env() -> dict:
    env = dict(os.environ, GH_CONFIG_DIR=os.path.join(DC, "gh"), GH_PROMPT_DISABLED="1",
               NO_COLOR="1", GIT_TERMINAL_PROMPT="0")
    t = gh_token()
    if t:
        env["GH_TOKEN"] = t
    return env


def gh(*args: str, capture: bool = True, check: bool = True, input: bytes | None = None,
       cwd: str | None = None) -> subprocess.CompletedProcess:
    return run([tool("gh"), *args], capture=capture, check=check, input=input, env=gh_env(), cwd=cwd)


def login() -> None:
    """Takes the token of a system gh if there is one, else signs in through the browser."""
    sys_gh = shutil.which("gh")
    if sys_gh and os.path.abspath(sys_gh) != os.path.abspath(tool("gh")):
        r = run([sys_gh, "auth", "token"], check=False)
        if r.returncode == 0 and out(r):
            write(TOKEN, out(r).encode() + b"\n", private=True)
            say("GitHub token taken from the system gh")
            return
    env = dict(gh_env(), GH_PROMPT_DISABLED="")
    env.pop("GH_TOKEN", None)
    subprocess.run([tool("gh"), "auth", "login", "--hostname", "github.com", "--git-protocol",
                    "https", "--web", "--insecure-storage", "--scopes", "repo,workflow"], env=env, check=True)
    t = out(run([tool("gh"), "auth", "token"], env=env))
    write(TOKEN, t.encode() + b"\n", private=True)


GIT_HELPER = '!f() { echo username=x-access-token; echo "password=$GH_TOKEN"; }; f'


def git(*args: str, cwd: str | None = None, capture: bool = True, check: bool = True,
        input: bytes | None = None) -> subprocess.CompletedProcess:
    """git with the devcloud token as credentials (never written into a remote URL)."""
    return run(["git", "-c", "credential.helper=", "-c", f"credential.helper={GIT_HELPER}", *args],
               cwd=cwd, capture=capture, check=check, input=input, env=gh_env())


def owner() -> str:
    o = os.environ.get("DC_OWNER")
    if o:
        return o
    r = run(["git", "-C", ROOT, "remote", "get-url", "origin"], check=False)
    m = re.search(r"github\.com[:/]([^/]+)/", out(r)) if r.returncode == 0 else None
    if not m:
        raise Fail("set DC_OWNER (no GitHub origin for devcloud)")
    return m.group(1)


def state_repo() -> str:
    return f"{owner()}/devcloud-state"


# ---------------------------------------------------------------- state repo

STATE_README = b"""# devcloud-state

Private state of [devcloud](https://github.com/%s/devcloud): `secrets/` (age-encrypted
.env files), `drive/` (manifests of the files kept in Google Drive), `claude/` (agent
configuration and memory), `projects.json`. Written by `dc`; do not edit by hand.
"""


def ensure_state() -> None:
    if os.path.isdir(os.path.join(STATE, ".git")):
        git("pull", "-q", "--rebase", cwd=STATE)
        return
    if not gh_token():
        # without a token "repo view" fails for an existing private repo too
        raise Fail("no GitHub token: set GH_TOKEN or run dc setup")
    repo = state_repo()
    if gh("repo", "view", repo, "--json", "name", check=False).returncode != 0:
        say(f"creating the private repo {repo}")
        gh("repo", "create", repo, "--private", "--description", "devcloud private state")
    git("clone", "-q", f"{GH}/{repo}.git", STATE)
    if not os.path.isfile(os.path.join(STATE, "README.md")):
        write(os.path.join(STATE, "README.md"), STATE_README % owner().encode())
        git("checkout", "-q", "-B", "main", cwd=STATE)
        commit_state("init", ["README.md"])


def commit_state(msg: str, paths: list[str]) -> None:
    git("add", "--", *paths, cwd=STATE)
    if git("diff", "--cached", "--quiet", cwd=STATE, check=False).returncode == 0:
        return
    git("-c", "user.name=devcloud", "-c", "user.email=devcloud@users.noreply.github.com",
        "commit", "-q", "-m", msg, cwd=STATE)
    if git("rev-parse", "-q", "--verify", "@{u}", cwd=STATE, check=False).returncode == 0:
        git("pull", "-q", "--rebase", cwd=STATE)
        git("push", "-q", cwd=STATE)
    else:
        git("push", "-q", "-u", "origin", "HEAD:main", cwd=STATE)


def load_sync() -> dict:
    try:
        return json.loads(read(SYNC))
    except (OSError, ValueError):
        return {}


def save_sync(d: dict) -> None:
    write(SYNC, json.dumps(d, indent=1, sort_keys=True).encode())


def decide(local: str | None, base: str | None, state: str | None) -> str:
    """Three-way choice for one synced file from its hashes: local now, at the last sync,
    in the state repo now. Returns same, take_state, take_local or conflict."""
    if local == state:
        return "same"
    if state is None or state == base:
        return "take_local" if local is not None else "same"
    if local is None or local == base:
        return "take_state"
    return "conflict"


# ---------------------------------------------------------------- encryption

def recipients() -> str:
    return os.path.join(STATE, "recipients.txt")


def ensure_key() -> None:
    if not os.path.isfile(KEY) and os.environ.get("DC_AGE_KEY"):
        write(KEY, os.environ["DC_AGE_KEY"].strip().encode() + b"\n", private=True)
    have_rec = os.path.isfile(recipients())
    if os.path.isfile(KEY):
        pub = out(run([tool("age-keygen"), "-y", KEY]))
        if have_rec and pub not in read(recipients()).decode():
            raise Fail(f"{KEY} does not match the state repo's recipients.txt")
        if not have_rec:
            write(recipients(), pub.encode() + b"\n")
            commit_state("age recipient", ["recipients.txt"])
        return
    if have_rec:
        raise Fail(f"no age key on this machine: copy {KEY} from another one (`dc key` prints it) "
                   "or set DC_AGE_KEY")
    say(f"new age key in {KEY}: keep a copy somewhere safe (`dc key` prints it)")
    run([tool("age-keygen"), "-o", KEY])
    if not WIN:
        os.chmod(KEY, 0o600)
    write(recipients(), out(run([tool("age-keygen"), "-y", KEY])).encode() + b"\n")
    commit_state("age recipient", ["recipients.txt"])


def encrypt(data: bytes) -> bytes:
    return run([tool("age"), "-R", recipients()], input=data).stdout


def decrypt(data: bytes) -> bytes:
    return run([tool("age"), "-d", "-i", KEY], input=data).stdout


# ---------------------------------------------------------------- projects

class Project:
    def __init__(self, root: str, name: str, repo: str | None):
        self.root, self.name, self.repo = root, name, repo


def project(path: str | None = None) -> Project | None:
    """The git repo around path (default: the working directory), named after its GitHub repo."""
    r = run(["git", "-C", path or os.getcwd(), "rev-parse", "--show-toplevel"], check=False)
    if r.returncode != 0:
        return None
    root = os.path.normpath(out(r))
    if os.path.normcase(root) == os.path.normcase(os.path.normpath(ROOT)):
        return None
    url = out(run(["git", "-C", root, "remote", "get-url", "origin"], check=False))
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$", url)
    repo = m.group(1) if m else None
    return Project(root, repo.split("/")[1] if repo else os.path.basename(root), repo)


def need_project() -> Project:
    p = project()
    if not p:
        raise Fail("run this inside a project (a git repo)")
    return p


def registry() -> dict:
    try:
        return json.loads(read(os.path.join(STATE, "projects.json")))
    except (OSError, ValueError):
        return {}


def register(p: Project) -> None:
    """Remembers where the project lives on this OS (not for sandboxes or CI)."""
    if EPHEMERAL:
        return
    reg = registry()
    e = reg.setdefault(p.name, {})
    if e.get("repo") == p.repo and e.get("paths", {}).get(OS) == p.root:
        return
    e["repo"] = p.repo
    e.setdefault("paths", {})[OS] = p.root
    write(os.path.join(STATE, "projects.json"), json.dumps(reg, indent=1, sort_keys=True).encode())
    commit_state(f"project {p.name}", ["projects.json"])


def known_projects() -> list[Project]:
    """The current project plus every registered one that exists on this machine."""
    seen, res = set(), []
    cur = project()
    if cur:
        register(cur)
        res.append(cur)
        seen.add(cur.name)
    for name, e in sorted(registry().items()):
        path = e.get("paths", {}).get(OS)
        if name not in seen and path and os.path.isdir(path):
            res.append(Project(path, name, e.get("repo")))
            seen.add(name)
    return res
