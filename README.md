# devcloud

One portable CLI, `dc`, that keeps my projects working the same on every machine (Windows PC,
Linux, cloud VMs, Claude Code web sessions, GitHub Actions runners):

- **secrets**: each project's `.env`, age-encrypted in a private state repo (`<owner>/devcloud-state`)
- **Drive**: copyrighted or big files in `<Google Drive>/Projects/<project>/`, checked by sha256, never in git
- **Claude setup**: `~/.claude` and `~/.codex` config, per-project memory and the projects' local agent files
- **CPU jobs**: `dc run -- CMD` runs on GitHub Actions in the project's repo (up to 6 h per job,
  parallel with `--each`); `dc brief` writes the same job as a task for a Claude Code web session
- **GPU jobs**: `dc gpu JOB.json --in FILE --out DIR` runs a project's script on a free cloud GPU:
  Modal, then Kaggle, then Colab (manual steps); the spec and the script's contract are in
  `devcloud/gpu.py`, the modal and kaggle CLIs go into `.dc/`

Everything it needs (uv, Python, age, gh, rclone; pinned by sha256) goes into `.dc/` next to it.
Nothing is installed on the system and nothing is read from PATH except git and bash.

## Bootstrap

```sh
git clone https://github.com/<owner>/devcloud && cd devcloud
./dc setup          # Windows: .\dc setup  (GitHub sign-in, state repo, age key)
```

The first machine creates the age key and the private state repo. On every other machine, give it
the key once: `dc key` prints it on a machine that has it; put it in `DC_AGE_KEY` (or paste it into
`.dc/age-key.txt`) before `dc setup`.

### Claude Code web environment

Environment variables: `DC_AGE_KEY` (from `dc key`) and `DC_GH_TOKEN` (a token with `repo` scope; `GH_TOKEN` also works
where the sandbox leaves it alone).
Setup script:

```sh
#!/bin/sh
git clone -q https://github.com/<owner>/devcloud ~/devcloud 2>/dev/null || git -C ~/devcloud pull -q
~/devcloud/dc setup --web
```

The script cannot read the environment's variables (only the session gets them), and the sandbox
puts placeholders in `GH_TOKEN`/`GITHUB_TOKEN`: hence `DC_GH_TOKEN`. `dc setup --web` installs the
tools and points the SessionStart/Stop/SessionEnd hooks at `web-boot.sh`, which at SessionStart
runs `dc setup` and `dc claude pull` (the stored settings and `dc-hook.sh` then take over, and
`dc secrets pull` brings the project's `.env`).

The session's GitHub access is bound to the repositories it was started with (other repos get
HTTP 403 from its proxy): start it with the project and `<owner>/devcloud-state` both selected.

Network allowlist: github.com, objects.githubusercontent.com,
release-assets.githubusercontent.com, pypi.org, files.pythonhosted.org, download.pytorch.org,
static.rust-lang.org, crates.io, static.crates.io, www.kaggle.com, kaggle.com, modal.com,
api.modal.com, www.googleapis.com, oauth2.googleapis.com, *.googleusercontent.com

The pull brings `~/.claude` (settings for Linux included), whose hooks run
`~/.claude/tools/dc-hook.sh`: `dc claude pull` at the start, `dc claude push` after every answer
(a sandbox can die at any time) and at the end. If the session hands off (`handoff.py`), the
hand-off goes into the project's memory, and the next session on the repo, cloud or PC, starts
from it.

## Use

```sh
dc secrets push            # in a project: its .env -> state repo (lines after "# dc: local" stay)
dc secrets pull
dc secrets gh              # global + project secrets -> the repo's Actions secret DC_ENV

dc drive login             # once, in a browser (rclone's shared client, retired by Google in 2026)
dc drive login ID SECRET   # same with your own OAuth client: Google Cloud > APIs > Drive API on,
                           # Credentials > OAuth client ID > Desktop app
dc drive push media/       # project files -> Drive, recorded in the manifest
dc drive pull              # everything in the manifest, verified
dc drive ls refs  /  dc drive get refs/a.flac in/  /  dc drive put out/ synced

dc claude push             # end of a session
dc claude pull             # start of a session

dc init                    # adds .github/workflows/dc.yml to the project (commit and push it);
                           # its own CI can call it too: `uses: ./.github/workflows/dc.yml`
dc run -- ./lls test all
dc run --in refs:in --out out:results -- ./lls batch in out
dc run --each "./lls test pal" "./lls test tempo"
dc brief -- ./lls batch in out

dc gpu gpu/separate.json --in src.flac --out stems/              # Modal -> Kaggle -> Colab
dc gpu --long gpu/separate.json --in src.flac --out stems/ -- --model M   # Kaggle first
```

`dc help` lists everything.

## Notes

- Artifacts and logs of a public repo are public. Anything copyrighted goes in and out through
  Drive (`--in`, `--out`), never `--artifact`.
- GPU tokens (`MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET`, `KAGGLE_USERNAME`/`KAGGLE_KEY`) live in
  the global secrets (`dc secrets set --global`). Modal runs with a 0 $ spend limit on the
  monthly credits; Kaggle's GPU quota is weekly.
- `DC_ENV` holds the Drive token, which can read the whole Drive. Workflows from forks never get
  repo secrets, but anyone with write access to the repo does.
- Deletions are not propagated by `dc claude`; a file changed on two machines keeps the local copy
  and leaves the other as `<name>.dc-remote`.

## License

MIT
