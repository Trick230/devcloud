"""GPU jobs on a free cloud GPU: Modal (monthly credits, spend limit 0 $), then Kaggle (GPU
quota, slower to start), then Colab (no API: prints the manual steps). --long tries Kaggle
first. A provider without its token, or that fails (quota, credits), passes the job on.

A job is a JSON spec in the project:

    {"name": "lls-sep",                  Modal app dc-<name>, Kaggle dataset/kernel dc-<name>
     "script": "separate.py",            next to the spec
     "files": ["../pkg/helper.py"],      shipped next to the script (flat, by base name)
     "pip": ["torch", "soundfile"],      "apt": ["build-essential"] (Modal only)
     "prepare": true,                    `script --prepare` once: Modal image build, Kaggle start
     "gpu": "T4", "kaggle_gpu": "NvidiaTeslaT4", "timeout": 3600}

The script runs as `python script IN_DIR OUT_DIR ARGS...` with the inputs in IN_DIR (by base
name) and writes its outputs into OUT_DIR, which come back into --out. DC_GPU_CACHE is a
folder for the job's cached data (Modal: baked into the image by --prepare).
Tokens: MODAL_TOKEN_ID/MODAL_TOKEN_SECRET, KAGGLE_USERNAME/KAGGLE_KEY in the environment or
the global/project secrets. The modal and kaggle CLIs go into .dc/<platform>/bin.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

from . import core, secrets
from .core import Fail, say

HERE = os.path.dirname(os.path.abspath(__file__))
CLIS = {"modal": "modal==1.6.4", "kaggle": "kaggle==2.2.5"}
TOKENS = ("MODAL_", "KAGGLE_")


class Skip(Exception):
    pass


def spec_of(path: str) -> dict:
    """The job spec with absolute 'dir' and 'ship' (script first, then files)."""
    try:
        s = json.loads(core.read(path))
    except (OSError, ValueError) as e:
        raise Fail(f"{path}: {e}")
    for k in ("name", "script"):
        if not s.get(k):
            raise Fail(f"{path}: no '{k}'")
    d = os.path.dirname(os.path.abspath(path))
    ship = [os.path.normpath(os.path.join(d, f)) for f in [s["script"], *s.get("files", [])]]
    for f in ship:
        if not os.path.isfile(f):
            raise Fail(f"{path}: {f} not found")
    names = [os.path.basename(f) for f in ship]
    if len(set(names)) != len(names):
        raise Fail(f"{path}: two shipped files share a base name")
    s.update(dir=d, ship=ship)
    return s


def cli(name: str) -> str | None:
    """The modal/kaggle CLI in .dc/<platform>/bin, installed with uv on first use."""
    exe = core.tool(name)
    if os.path.isfile(exe):
        return exe
    uv = core.tool("uv")
    if not os.path.isfile(uv):
        return shutil.which(name)
    env = dict(os.environ, UV_TOOL_DIR=os.path.join(core.LOCAL, "tools"), UV_TOOL_BIN_DIR=core.BIN)
    env.pop("VIRTUAL_ENV", None)
    say(f"installing the {name} CLI")
    r = subprocess.run([uv, "tool", "install", "-q", "--python", "3.14", CLIS[name]], env=env,
                       stdin=subprocess.DEVNULL, stdout=sys.stderr)
    return exe if r.returncode == 0 and os.path.isfile(exe) else shutil.which(name)


def token_env(p: core.Project | None) -> dict:
    """The environment plus the provider tokens from the secrets (empty values skipped: they
    would hide ~/.modal.toml or kaggle.json)."""
    env = dict(os.environ)
    try:
        stored = secrets.env_for(p)
    except Fail:
        stored = {}
    for k, v in stored.items():
        if k.startswith(TOKENS) and v and not env.get(k):
            env[k] = v
    return env


def _run(cmd: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, capture_output=True, stdin=subprocess.DEVNULL, env=env)


def _bundle(s: dict, d: str, extra: dict) -> None:
    os.makedirs(d, exist_ok=True)
    for f in s["ship"]:
        shutil.copy(f, d)
    meta = {k: v for k, v in s.items() if k not in ("dir", "ship")}
    with open(os.path.join(d, "job.json"), "w") as f:
        json.dump({**meta, **extra}, f)


def modal(s: dict, ins: list[str], out: str, args: list[str], env: dict) -> None:
    exe = cli("modal")
    if not exe:
        raise Skip("no modal CLI")
    if not (env.get("MODAL_TOKEN_ID") or os.path.isfile(os.path.expanduser("~/.modal.toml"))):
        raise Skip("no Modal token (MODAL_TOKEN_ID/MODAL_TOKEN_SECRET)")
    with tempfile.TemporaryDirectory() as d:
        _bundle(s, d, {})
        e = dict(env, DC_GPU_JOB=d, DC_GPU_IN=json.dumps(ins), DC_GPU_OUT=out,
                 DC_GPU_ARGS=json.dumps(args), PYTHONIOENCODING="utf-8")
        rc = subprocess.call([exe, "run", os.path.join(HERE, "gpu_modal.py")], env=e,
                             stdin=subprocess.DEVNULL, stdout=sys.stderr)
    if rc != 0:
        raise Skip(f"modal run failed ({rc})")


def _kaggle_user(env: dict) -> str:
    if env.get("KAGGLE_USERNAME"):
        return env["KAGGLE_USERNAME"]
    p = os.path.join(env.get("KAGGLE_CONFIG_DIR", os.path.expanduser("~/.kaggle")), "kaggle.json")
    if os.path.isfile(p):
        with open(p) as f:
            return json.load(f)["username"]
    raise Skip("no Kaggle token (KAGGLE_USERNAME/KAGGLE_KEY)")


def kaggle(s: dict, ins: list[str], out: str, args: list[str], env: dict) -> None:
    """The job and its inputs as a new version of a private dataset, gpu_kaggle.py as a
    private GPU kernel reading it; waits for the kernel and downloads its outputs."""
    exe = cli("kaggle")
    if not exe:
        raise Skip("no kaggle CLI")
    user = _kaggle_user(env)
    slug = f"dc-{s['name']}"
    ds, kn = f"{user}/{slug}-in", f"{user}/{slug}"
    with tempfile.TemporaryDirectory() as d:
        data, kern, res = (os.path.join(d, x) for x in ("data", "kernel", "out"))
        nonce = os.urandom(8).hex()      # the outputs must come from this upload
        names = [os.path.basename(f) for f in ins]
        reserved = {os.path.basename(f) for f in s["ship"]} | {"job.json", "dataset-metadata.json"}
        if set(names) & reserved:
            raise Skip("an input has the name of a job file")
        _bundle(s, data, {"nonce": nonce, "args": args, "inputs": names})
        for f in ins:
            shutil.copy(f, data)
        with open(os.path.join(data, "dataset-metadata.json"), "w") as f:
            json.dump({"title": f"{slug}-in", "id": ds, "licenses": [{"name": "CC0-1.0"}]}, f)
        if _run([exe, "datasets", "status", ds], env).returncode == 0:
            r = _run([exe, "datasets", "version", "-p", data, "-m", "job", "-q"], env)
        else:
            r = _run([exe, "datasets", "create", "-p", data, "-q"], env)   # private by default
        if r.returncode != 0:
            raise Skip(f"kaggle dataset upload failed: {(r.stderr or r.stdout).strip()[-300:]}")
        say("kaggle: inputs uploaded, waiting for the dataset")
        _kaggle_wait(exe, env, ["datasets", "status", ds], ("ready",), ("error",), 900)
        os.makedirs(kern)
        shutil.copy(os.path.join(HERE, "gpu_kaggle.py"), kern)
        with open(os.path.join(kern, "kernel-metadata.json"), "w") as f:
            json.dump({"id": kn, "title": slug, "code_file": "gpu_kaggle.py",
                       "language": "python", "kernel_type": "script", "is_private": True,
                       "enable_gpu": True, "enable_internet": True,
                       "machine_shape": s.get("kaggle_gpu", "NvidiaTeslaT4"),
                       "dataset_sources": [ds]}, f)
        r = _run([exe, "kernels", "push", "-p", kern], env)
        if r.returncode != 0 or "error" in r.stdout.lower():
            raise Skip(f"kaggle kernel push failed: {(r.stderr or r.stdout).strip()[-300:]}")
        say("kaggle: kernel running")
        _kaggle_wait(exe, env, ["kernels", "status", kn], ("complete",), ("error", "cancel"),
                     max(3600, int(s.get("timeout", 3600))) * 3)
        os.makedirs(res)
        r = _run([exe, "kernels", "output", kn, "-p", res], env)
        try:
            with open(os.path.join(res, "dc-out.json")) as f:
                got = json.load(f)
        except (OSError, ValueError):
            raise Skip(f"kaggle: no outputs {(r.stderr or r.stdout).strip()[-300:]}")
        if got.get("nonce") != nonce:
            raise Skip("kaggle: the outputs are another job's (two jobs at once?)")
        if got.get("rc"):
            raise Skip(f"kaggle: the job failed ({got['rc']})")
        for n in got["files"]:
            shutil.move(os.path.join(res, n), os.path.join(out, n))


def _kaggle_wait(exe, env, args, ok, bad, timeout) -> None:
    end = time.time() + timeout
    while time.time() < end:
        time.sleep(20)                   # the status right after a push is the previous run's
        st = (_run([exe, *args], env).stdout or "").lower()
        if any(w in st for w in ok):
            return
        if any(w in st for w in bad):
            raise Skip(f"kaggle: {st.strip()[-300:]}")
    raise Skip("kaggle: timed out")


def colab(s: dict, ins: list[str], out: str, args: list[str], env: dict) -> None:
    files = " ".join(os.path.basename(f) for f in s["ship"] + ins)
    pip = " ".join(f'"{x}"' for x in s.get("pip", []))
    raise Skip(
        f"Colab has no job API; by hand: open a GPU notebook, upload {files}, then run "
        f"'!pip install -q {pip}' and "
        f"'!mkdir -p in out && mv {' '.join(os.path.basename(f) for f in ins)} in/ && "
        f"python {s['script']} in out {' '.join(args)}' and download out/* to {out}")


def dispatch(p: core.Project | None, spec: str, ins: list[str], out: str, args: list[str],
             long: bool) -> int:
    s = spec_of(spec)
    ins = [os.path.abspath(f) for f in ins]
    for f in ins:
        if not os.path.isfile(f):
            raise Fail(f"{f}: no such file")
    if len({os.path.basename(f) for f in ins}) != len(ins):
        raise Fail("two inputs share a base name")
    out = os.path.abspath(out)
    os.makedirs(out, exist_ok=True)
    env = token_env(p)
    for prov in ([kaggle, modal, colab] if long else [modal, kaggle, colab]):
        try:
            say(f"gpu: trying {prov.__name__}")
            prov(s, ins, out, args, env)
            say(f"gpu: {prov.__name__} done")
            return 0
        except Skip as e:
            say(f"gpu: {prov.__name__}: {e}")
    return 1
