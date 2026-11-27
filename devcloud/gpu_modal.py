"""Modal app for `dc gpu`: one job (gpu.py's spec, bundled into DC_GPU_JOB) on a cloud GPU.
The inputs go as bytes in the call and the outputs come back the same way (no Volumes: they
are billed even with a 0 $ spend limit); `script --prepare` runs at image build, so its
downloads are baked in. Run by gpu.py as `modal run gpu_modal.py` with DC_GPU_* set.
"""
import json
import os
import pathlib

import modal

JOB = "/job"
CACHE = "/cache"
# locally the bundle is DC_GPU_JOB; in the container the same files are at /job
SRC = pathlib.Path(os.environ["DC_GPU_JOB"]) if modal.is_local() else pathlib.Path(JOB)
spec = json.loads((SRC / "job.json").read_text())

image = modal.Image.debian_slim(python_version=spec.get("python", "3.11"))
if spec.get("apt"):
    image = image.apt_install(*spec["apt"])
if spec.get("pip"):
    image = image.pip_install(*spec["pip"])
image = image.env({"DC_GPU_CACHE": CACHE}).add_local_dir(str(SRC), JOB, copy=True)
if spec.get("prepare"):
    image = image.run_commands(f"cd {JOB} && python {spec['script']} --prepare")
app = modal.App(f"dc-{spec['name']}", image=image)


@app.function(gpu=os.environ.get("DC_GPU_TYPE") or spec.get("gpu", "T4"),
              timeout=int(spec.get("timeout", 3600)))
def job(files: dict, args: list) -> dict:
    import subprocess
    import sys
    import tempfile
    i, o = tempfile.mkdtemp(), tempfile.mkdtemp()
    for n, b in files.items():
        pathlib.Path(i, n).write_bytes(b)
    rc = subprocess.call([sys.executable, spec["script"], i, o, *args], cwd=JOB)
    if rc:
        return {"rc": rc}
    return {"files": {n: pathlib.Path(o, n).read_bytes() for n in os.listdir(o)
                      if pathlib.Path(o, n).is_file()}}


@app.local_entrypoint()
def main():
    ins = json.loads(os.environ["DC_GPU_IN"])
    out = pathlib.Path(os.environ["DC_GPU_OUT"])
    files = {os.path.basename(f): pathlib.Path(f).read_bytes() for f in ins}
    print(f"modal: {sum(map(len, files.values())) / 1e6:.0f} MB up")
    res = job.remote(files, json.loads(os.environ["DC_GPU_ARGS"]))
    # the job's own exit code goes to gpu.py in a file: a failed job is not a provider failure
    pathlib.Path(os.environ["DC_GPU_STATUS"]).write_text(json.dumps({"rc": res.get("rc", 0)}))
    for n, b in res.get("files", {}).items():
        if n not in ("", ".", "..") and "/" not in n and "\\" not in n and ":" not in n:
            (out / n).write_bytes(b)        # names come from the remote side: never a path
