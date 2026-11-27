"""Paths, the pinned tools (age, gh, rclone), GitHub auth and the private state repo.

Everything lives in .dc/ next to this repo: .dc/<platform>/ (uv, Python, tools), .dc/state
(a clone of <owner>/devcloud-state), .dc/age-key.txt (the one secret), .dc/gh-token and
.dc/sync.json (what was last synced, per machine). Standard library only.
"""
from __future__ import annotations

import calendar
import hashlib
import hmac
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from functools import lru_cache

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


@lru_cache(maxsize=None)
def _user_sid() -> str:
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "whoami.exe")
    r = subprocess.run([exe, "/user", "/fo", "csv", "/nh"], capture_output=True, text=True)
    sid = r.stdout.strip().rsplit(",", 1)[-1].strip().strip('"')
    if r.returncode != 0 or not sid.startswith("S-1-"):
        raise Fail(f"whoami /user: no SID ({r.stderr.strip()[-200:]})")
    return sid


def restrict(path: str) -> None:
    """Windows: only this user (and SYSTEM) may open path. A file under D:\\ otherwise inherits
    read for every local user and write for every signed-in one; chmod does nothing there."""
    if not WIN or not os.path.exists(path):
        return
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "icacls.exe")
    inh = "(OI)(CI)" if os.path.isdir(path) else ""   # a folder: what it holds inherits it
    r = subprocess.run([exe, path, "/inheritance:r", "/grant:r", f"*{_user_sid()}:{inh}F",
                        "/grant:r", f"*S-1-5-18:{inh}F"], capture_output=True, text=True)
    if r.returncode != 0:
        raise Fail(f"icacls {path}: {(r.stdout + r.stderr).strip()[-200:]}")


def private_files() -> list[str]:
    """The secrets dc keeps on this machine: age key, GitHub token, saved accounts, .env files."""
    files = [DC]   # the key, token, accounts and state repo inherit from it
    files += [os.path.join(p.root, ".env") for p in known_projects()]
    return [f for f in dict.fromkeys(files) if os.path.exists(f)]


def write(path: str, data: bytes, private: bool = False) -> None:
    """Writes a new file next to the old one and swaps it in: a reader that has the old one
    open (a running hook script, which sh reads as it runs) keeps reading the old content."""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, f".{os.path.basename(path)}.dc-tmp")
    if os.path.exists(tmp):
        os.remove(tmp)
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
                           0o600 if private else 0o666), "wb") as f:
        if private:
            restrict(tmp)               # before a byte of the secret is in it
        f.write(data)
    if not WIN:
        mode = 0o600 if private else (os.stat(path).st_mode & 0o7777 if os.path.exists(path) else None)
        if mode is not None:
            os.chmod(tmp, mode)
    try:
        os.replace(tmp, path)
    except OSError:   # Windows refuses to replace a file another process holds open
        os.remove(tmp)
        if private:
            restrict(path)
        with open(path, "wb") as f:
            f.write(data)


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
    # DC_GH_TOKEN first: a web sandbox may reserve or clear the GitHub names
    t = os.environ.get("DC_GH_TOKEN") or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if t:
        return t
    try:
        return read(TOKEN).decode().strip() or None
    except OSError:
        return None


TOKEN_WARN = 14 * 86400     # warn this long before the GitHub token expires
TOKEN_CACHE = 86400         # ask GitHub at most once a day


def token_expiry(refresh: bool = False) -> float | None:
    """Expiry (epoch) of the GitHub token, None if it never expires or is unknown. GitHub
    sends it in a header on every authenticated call; cached in .dc/<plat>/gh-token-expiry
    (keyed by the token's hash) so a session start reads a file, and only refresh asks."""
    t = gh_token()
    if not t:
        return None
    key = sha(t.encode())[:16]
    cache = os.path.join(LOCAL, "gh-token-expiry")
    try:
        k, exp, at = read(cache).decode().split()
        if k == key and (not refresh or time.time() - float(at) < TOKEN_CACHE):
            return float(exp) or None
    except (OSError, ValueError):
        if not refresh:
            return None
    req = urllib.request.Request("https://api.github.com/rate_limit", headers={
        "Authorization": f"Bearer {t}", "User-Agent": "devcloud"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            h = r.headers.get("github-authentication-token-expiration")
    except OSError:
        return None
    exp = 0.0
    if h:   # "2027-02-24 12:00:00 UTC" (or +0000)
        exp = float(calendar.timegm(time.strptime(h[:19], "%Y-%m-%d %H:%M:%S")))
    write(cache, f"{key} {exp:.0f} {time.time():.0f}\n".encode())
    return exp or None


def token_warning(exp: float | None, now: float) -> str | None:
    if exp is None or exp - now > TOKEN_WARN:
        return None
    days = int((exp - now) // 86400)
    when = time.strftime("%Y-%m-%d", time.gmtime(exp))
    state = f"expired on {when}" if days < 0 else f"expires in {days} day(s) ({when})"
    return (f"GitHub token {state}: regenerate it at github.com/settings/tokens, put it in "
            ".dc/gh-token and DC_GH_TOKEN of each web environment (dc web-env)")


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
    say("this token reaches every repo of the account; a narrower one: a fine-grained token "
        "(github.com/settings/tokens, only the repos dc uses) in .dc/gh-token")


GIT_HELPER = '!f() { echo username=x-access-token; echo "password=$GH_TOKEN"; }; f'


def git(*args: str, cwd: str | None = None, capture: bool = True, check: bool = True,
        input: bytes | None = None) -> subprocess.CompletedProcess:
    """git with the devcloud token as credentials (never written into a remote URL)."""
    return run(["git", "-c", "credential.helper=", "-c", f"credential.{GH}.helper={GIT_HELPER}", *args],
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


_LOCKED = False


def _alive(pid_text: bytes) -> bool:
    """Whether the process that wrote a lock still runs (unreadable or unknown: assume it does)."""
    try:
        pid = int(pid_text.strip())
    except ValueError:
        return True
    if os.name == "nt":   # os.kill(pid, 0) would send CTRL_C here, not probe
        import ctypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        h = k.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.get_last_error() == 5   # access denied: it exists
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return not ok or code.value == 259        # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def lock_state(wait: float = 180, stale: float = 600) -> None:
    """One dc process at a time on the state repo, held until exit: two at once (a hook's
    `dc report push` and the Stop hook's `dc claude push`) run `git pull --rebase` together and
    break it on the shared FETCH_HEAD ("Cannot rebase onto multiple branches")."""
    global _LOCKED
    if _LOCKED:
        return
    import atexit
    import time
    lock = os.path.join(DC, "state.lock")
    os.makedirs(DC, exist_ok=True)
    end = time.time() + wait
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(lock) > stale or not _alive(read(lock)):   # a killed dc left it
                    os.remove(lock)
                    continue
            except OSError:
                continue
            if time.time() > end:
                raise Fail(f"the state repo is busy ({lock}; delete it if no dc is running)")
            time.sleep(0.5)
    _LOCKED = True
    atexit.register(lambda: os.path.exists(lock) and os.remove(lock))


def pull_state() -> None:
    """Rebase this machine's state commits onto the remote. On a conflict the local version wins
    (-X theirs, in a rebase: the commit being replayed): the state's files are rewritten whole
    and claude push has already merged them; a rebase that still fails is aborted, so the repo
    is never left half-way."""
    r = git("pull", "-q", "--rebase", "-X", "theirs", cwd=STATE, check=False)
    if r.returncode == 0:
        return
    if any(os.path.isdir(os.path.join(STATE, ".git", d)) for d in ("rebase-merge", "rebase-apply")):
        git("rebase", "--abort", cwd=STATE, check=False)
    err = (r.stderr or b"").decode(errors="replace").strip()[-600:]
    raise Fail(f"git pull --rebase failed in {STATE}: {err}")


def ensure_state() -> None:
    lock_state()
    if os.path.isdir(os.path.join(STATE, ".git")):
        pull_state()
        return
    if not gh_token():
        # without a token "repo view" fails for an existing private repo too
        raise Fail("no GitHub token: set DC_GH_TOKEN or run dc setup")
    repo = state_repo()
    url = f"{GH}/{repo}.git"
    if git("ls-remote", "-q", url, "HEAD", check=False).returncode != 0:
        # create only when GitHub says it does not exist: a web sandbox's proxy also refuses
        # repos the session was not given (HTTP 403), and that must not look like "missing"
        r = gh("repo", "view", repo, "--json", "name", check=False)
        err = (r.stderr or b"").decode(errors="replace")
        if r.returncode == 0 or "Could not resolve to a Repository" not in err:
            raise Fail(f"cannot reach {repo} (in a Claude Code web session, add it to the "
                       f"session's repositories){': ' + err.strip()[-400:] if err.strip() else ''}")
        say(f"creating the private repo {repo}")
        gh("repo", "create", repo, "--private", "--description", "devcloud private state")
    git("clone", "-q", url, STATE)
    if not os.path.isfile(os.path.join(STATE, "README.md")):
        write(os.path.join(STATE, "README.md"), STATE_README % owner().encode())
        git("checkout", "-q", "-B", "main", cwd=STATE)
        commit_state("init", ["README.md"])


def commit_state(msg: str, paths: list[str]) -> None:
    git("add", "--", *paths, cwd=STATE)
    upstream = git("rev-parse", "-q", "--verify", "@{u}", cwd=STATE, check=False).returncode == 0
    if git("diff", "--cached", "--quiet", cwd=STATE, check=False).returncode == 0:
        # nothing new, but commits an earlier failed push left behind still go out
        ahead = upstream and git("rev-list", "--count", "@{u}..HEAD", cwd=STATE,
                                 check=False).stdout.strip() not in (b"", b"0")
        if not ahead:
            return
    else:
        git("-c", "user.name=devcloud", "-c", "user.email=devcloud@users.noreply.github.com",
            "commit", "-q", "-m", msg, cwd=STATE)
    if upstream:
        pull_state()
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


def recipient_keys() -> list[str]:
    """The public keys in recipients.txt (comments and blank lines skipped)."""
    try:
        text = read(recipients()).decode()
    except OSError:
        return []
    return [s.strip() for s in text.splitlines() if s.strip() and not s.lstrip().startswith("#")]


def only_recipient(keys: list[str], pub: str | None = None) -> str:
    """The single recipient, or Fail: anyone who can write the state repo could otherwise add
    a key of their own and receive every secret stored after that."""
    if len(keys) != 1:
        raise Fail(f"recipients.txt in the state repo must hold exactly one key (it has {len(keys)}): "
                   "check its history, someone may have added one")
    if pub is not None and keys[0] != pub:
        raise Fail(f"{KEY} does not match the state repo's recipients.txt (key rotated? copy "
                   "the new one: `dc key` on the machine that rotated it)")
    return keys[0]


def ensure_key() -> None:
    env_key = os.environ.get("DC_AGE_KEY", "").strip()
    have_rec = os.path.isfile(recipients())
    if env_key and (not os.path.isfile(KEY) or env_key not in read(KEY).decode()):
        # the environment replaces a key file only when the state repo takes its key (a restored
        # web VM after a rotation), never a newer key with a stale variable
        env_pub = out(run([tool("age-keygen"), "-y"], input=env_key.encode() + b"\n", check=False))
        if not os.path.isfile(KEY) or (have_rec and env_pub and recipient_keys() == [env_pub]):
            write(KEY, env_key.encode() + b"\n", private=True)
    if os.path.isfile(KEY):
        pub = out(run([tool("age-keygen"), "-y", KEY]))
        if have_rec:
            only_recipient(recipient_keys(), pub)
        else:
            write(recipients(), pub.encode() + b"\n")
            commit_state("age recipient", ["recipients.txt"])
        return
    if have_rec:
        raise Fail(f"no age key on this machine: copy {KEY} from another one (`dc key` prints it) "
                   "or set DC_AGE_KEY")
    say(f"new age key in {KEY}: keep a copy somewhere safe (`dc key` prints it)")
    os.makedirs(DC, exist_ok=True)
    restrict(DC)                 # the new key inherits this user's ACL from the first byte
    run([tool("age-keygen"), "-o", KEY])
    if not WIN:
        os.chmod(KEY, 0o600)
    write(recipients(), out(run([tool("age-keygen"), "-y", KEY])).encode() + b"\n")
    commit_state("age recipient", ["recipients.txt"])


def rotate_key() -> list[str]:
    """A new age key: every .age file of the state repo decrypted with the old one (all before
    anything changes), re-encrypted to the new one, recipients.txt replaced, one commit. The old
    key stays as age-key.old.txt until the push went through. Returns the re-encrypted paths."""
    if os.environ.get("DC_AGE_KEY") or EPHEMERAL:
        raise Fail("rotate on a lasting machine with the key file (not where DC_AGE_KEY sets it)")
    ensure_state()
    ensure_key()
    rels = [f for f in out(git("ls-files", "*.age", cwd=STATE)).splitlines() if f]
    plain = {f: decrypt(read(os.path.join(STATE, f))) for f in rels}
    old_mac = mac_key()
    vouched = [f for f in out(git("ls-files", cwd=STATE)).splitlines() if f and signed(f, old_mac)]
    new, old = KEY + ".new", os.path.join(DC, "age-key.old.txt")
    if os.path.exists(new) or os.path.exists(old):
        raise Fail(f"{new} or {old} left by an unfinished rotation: check which key opens the "
                   "state repo's files before removing either")
    run([tool("age-keygen"), "-o", new])
    restrict(new)
    pub = out(run([tool("age-keygen"), "-y", new]))
    try:
        write(recipients(), pub.encode() + b"\n")
        for f, data in plain.items():
            write(os.path.join(STATE, f), encrypt(data))
        os.replace(KEY, old)
    except BaseException:
        # back to the old key's files: nothing half-rotated stays in the working tree
        git("checkout", "--", "recipients.txt", *rels, cwd=STATE, check=False)
        os.remove(new)
        raise
    os.replace(new, KEY)
    if not WIN:
        os.chmod(KEY, 0o600)
    new_mac = mac_key(KEY)
    sigs = [s for s in (sign(f, new_mac) for f in vouched) if s]
    commit_state("age key rotated", ["recipients.txt", *rels, *sigs])
    os.remove(old)
    return rels


SIGS = "sigs"   # sigs/<rel>.sig: a machine holding the age key stored <rel> as it is now


def mac_key(path: str | None = None) -> bytes:
    """A MAC key only holders of the age key can derive (the GitHub token is not enough, and
    age itself lets anyone with the public key encrypt)."""
    if path is None:
        ensure_key()
    secret = next((ln.strip() for ln in read(path or KEY).decode().splitlines()
                   if ln.strip().startswith("AGE-SECRET-KEY-")), "")
    if not secret:
        raise Fail(f"no secret key in {path or KEY}")
    return hashlib.sha256(b"devcloud signed state\0" + secret.encode()).digest()


def _sig_file(rel: str) -> str:
    return os.path.join(STATE, SIGS, *rel.split("/")) + ".sig"


def _mac(rel: str, digest: str, key: bytes) -> str:
    return hmac.new(key, f"{rel}\0{digest}".encode(), "sha256").hexdigest()


def signed(rel: str, key: bytes) -> bool:
    """The state file rel is as a machine holding the age key stored it."""
    digest = file_sha(os.path.join(STATE, *rel.split("/")))
    try:
        mac = read(_sig_file(rel)).decode().strip()
    except (OSError, UnicodeDecodeError):
        return False
    return bool(digest) and hmac.compare_digest(mac, _mac(rel, digest or "", key))


def sign(rel: str, key: bytes) -> str | None:
    """Vouches for the state file rel as it is now; the signature's state path when it changed."""
    digest = file_sha(os.path.join(STATE, *rel.split("/")))
    if not digest or signed(rel, key):
        return None
    write(_sig_file(rel), _mac(rel, digest, key).encode() + b"\n")
    return f"{SIGS}/{rel}.sig"


def unsigned_files(key: bytes) -> list[str]:
    """Tracked state files without a valid signature (signatures, sessions and reports aside)."""
    files = out(git("ls-files", cwd=STATE)).splitlines()
    return [f for f in files if f and not f.startswith((f"{SIGS}/", "sessions/", "reports/"))
            and f not in ("README.md", ".gitignore") and not signed(f, key)]


def trust() -> list[str]:
    """Signs every state file as it is now: once, after reading them (git log in the state
    repo), when signatures are first introduced or something was stored without the key."""
    ensure_state()
    key = mac_key()
    rels = unsigned_files(key)
    sigs = [s for s in (sign(r, key) for r in rels) if s]
    if sigs:
        commit_state(f"trust {len(sigs)} file(s)", sigs)
    return rels


def encrypt(data: bytes) -> bytes:
    return run([tool("age"), "-r", only_recipient(recipient_keys())], input=data).stdout


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
