"""Kaggle kernel for `dc gpu`: finds the job in its input dataset (job.json, the shipped files
and the inputs, flat), installs its pip packages, runs `script --prepare` if asked, then
`script IN OUT ARGS...`. The outputs go into /kaggle/working with dc-out.json (names, the
upload's nonce, the exit code), which `kaggle kernels output` brings back.
"""
import glob
import json
import os
import shutil
import subprocess
import sys

WORK = "/kaggle/working"

# the input dataset's mount point has changed across Kaggle versions: search for it
spec_path = (glob.glob("/kaggle/input/**/job.json", recursive=True) or [None])[0]
if not spec_path:
    raise SystemExit("job.json not in /kaggle/input")
src = os.path.dirname(spec_path)
with open(spec_path) as f:
    spec = json.load(f)

job, inp, out = "/tmp/dc/job", "/tmp/dc/in", "/tmp/dc/out"
for d in (job, inp, out):
    os.makedirs(d, exist_ok=True)
for n in os.listdir(src):                # the dataset is read-only: the job runs from a copy
    if os.path.isfile(os.path.join(src, n)):
        shutil.copy(os.path.join(src, n), inp if n in spec["inputs"] else job)
env = dict(os.environ, DC_GPU_CACHE="/tmp/dc/cache")
rc = 0
if spec.get("pip"):                      # already installed packages (torch) are kept
    rc = subprocess.call([sys.executable, "-m", "pip", "install", "-q", *spec["pip"]])
if rc == 0 and spec.get("prepare"):
    rc = subprocess.call([sys.executable, spec["script"], "--prepare"], cwd=job, env=env)
if rc == 0:
    rc = subprocess.call([sys.executable, spec["script"], inp, out, *spec.get("args", [])],
                         cwd=job, env=env)
files = []
for n in os.listdir(out):
    if os.path.isfile(os.path.join(out, n)) and n != "dc-out.json":
        shutil.move(os.path.join(out, n), os.path.join(WORK, n))
        files.append(n)
with open(os.path.join(WORK, "dc-out.json"), "w") as f:
    json.dump({"nonce": spec.get("nonce"), "rc": rc, "files": files}, f)
