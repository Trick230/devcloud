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
    subprocess.check_call([sys.executable, spec["script"], i, o, *args], cwd=JOB)
    return {n: pathlib.Path(o, n).read_bytes() for n in os.listdir(o)
            if pathlib.Path(o, n).is_file()}


@app.local_entrypoint()
def main():
    ins = json.loads(os.environ["DC_GPU_IN"])
    out = pathlib.Path(os.environ["DC_GPU_OUT"])
    files = {os.path.basename(f): pathlib.Path(f).read_bytes() for f in ins}
    print(f"modal: {sum(map(len, files.values())) / 1e6:.0f} MB up")
    for n, b in job.remote(files, json.loads(os.environ["DC_GPU_ARGS"])).items():
        (out / n).write_bytes(b)
