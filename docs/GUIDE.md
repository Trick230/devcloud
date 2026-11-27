# devcloud guide

The full reference behind the [README](../README.md): every command group, and the Claude Code
web environment in detail. `dc help` lists every option.

- [Requirements](#requirements)
- [Commands](#commands)
- [Claude Code web environments](#claude-code-web-environments)

## Requirements

- Windows 10/11 or Linux, x64 or ARM64
- `git`, and `bash` (Git Bash on Windows)
- A GitHub account; optionally a Google account (Drive), Modal and Kaggle accounts (GPU)

## Commands

### Secrets

```sh
dc secrets push                 # the project's .env -> state repo
dc secrets pull                 # state repo -> the project's .env (--force on a conflict)
dc secrets set [--global] KEY=VALUE...
dc secrets gh                   # project secrets + the global groups named in DC_GLOBAL
                                # (e.g. DC_GLOBAL=drive modal kaggle) -> Actions secret DC_ENV
dc key rotate                   # new age key, every stored secret re-encrypted
```

### Google Drive

```sh
dc drive login ID SECRET        # once, with your own OAuth client (Desktop app, published)
dc drive push media/            # project files -> Drive, recorded in the manifest
dc drive pull                   # everything in the manifest, verified by sha256
dc drive ls refs                # also: get REMOTE [DIR], put LOCAL REMOTE, forget PATH
```

The Drive scope is `drive.file`: `dc` only sees the files it uploaded itself.

### Agent configuration and accounts

```sh
dc claude pull | push           # ~/.claude, memory and agent files (the hooks run these)
dc account save NAME            # keep the signed-in account (this machine only)
dc account use NAME             # switch without the browser (new sessions, claude --resume)
dc report                       # the project's last web-session boot report
```

### CPU jobs on GitHub Actions

```sh
dc init                         # adds .github/workflows/dc.yml to the project (commit and push it)
dc run -- ./lls test all
dc run --in refs:in --out out:results -- ./lls batch in out
dc run --each "./lls test pal" "./lls test tempo"
dc brief -- ./lls batch in out  # the same job as a task for a Claude Code web session
dc gc --all [--dry-run]         # Actions history: newest success of each workflow (max 6),
                                # failures kept one day; runs at session end
```

A job runs the pushed commit; `--in` and `--out` move files through Drive.

### GPU jobs

```sh
dc gpu gpu/separate.json --in src.flac --out stems/                      # Modal -> Kaggle -> Colab
dc gpu --long gpu/separate.json --in src.flac --out stems/ -- --model M  # Kaggle first
```

The job spec and the script's contract are documented in `devcloud/gpu.py`.

In a Claude Code web session the sandbox's proxy cannot reach Modal or Kaggle, so `dc gpu`
relays the job by itself: the spec, its files and the inputs go to the project's Drive folder
(`gpu-relay/<id>`), a `dc run` job on the default branch runs `dc gpu` there, and the outputs
come back into `--out`. The current work need not be pushed; the project needs `dc init`
(its workflow on the default branch) and `dc secrets gh`. The Drive folder is removed afterwards.

## Claude Code web environments

Create one environment per Claude account. `dc web-env` prints everything to paste into
claude.ai, with the real values: variables, setup script and network allowlist.

| Setting | Value |
|---|---|
| Variables | `DC_AGE_KEY` (from `dc key`), `DC_GH_TOKEN` (fine-grained token: devcloud, devcloud-state and the project repos; Contents, Actions, Secrets and Workflows read/write) |
| Setup script | clones or updates devcloud, then `~/devcloud/dc setup --web` |
| Repositories | start each session with the project **and** `<owner>/devcloud-state` selected (other repos get HTTP 403 from the session proxy) |
| Network | github.com, objects.githubusercontent.com, release-assets.githubusercontent.com, pypi.org, files.pythonhosted.org, download.pytorch.org, static.rust-lang.org, crates.io, static.crates.io, www.kaggle.com, kaggle.com, modal.com, api.modal.com, www.googleapis.com, oauth2.googleapis.com, *.googleusercontent.com |

```sh
#!/bin/sh
git clone -q https://github.com/<owner>/devcloud ~/devcloud 2>/dev/null || git -C ~/devcloud pull -q
~/devcloud/dc setup --web
# optional, one per project with a heavy setup (kept in the environment's cached image):
#   ~/devcloud/dc setup --web --prep <owner>/<repo>="<command>"
```

How a session boots:

1. The setup script installs the tools and points the session hooks at `web-boot.sh`. It cannot
   read the environment's variables (only the session gets them), which is why the token is in
   `DC_GH_TOKEN`: the sandbox puts placeholders in `GH_TOKEN`/`GITHUB_TOKEN`.
2. At SessionStart, `web-boot.sh` runs `dc setup` and `dc claude pull`; the synchronised hooks
   (`~/.claude/tools/dc-hook.sh`) take over from there and `dc secrets pull` brings the `.env`.
   `dc session start` replaces the sandbox's git identity (Claude) with the token's GitHub
   account and its noreply address; `DC_GIT_NAME`/`DC_GIT_EMAIL` in the environment override it.
3. The project's `.claude/web-setup.sh`, if any, runs in the background (log in
   `$TMPDIR/dc-web-setup-<project>.log`). With `--prep`, heavy downloads are pre-built into the
   cached image under `~/dc-prep/<repo>`, exposed to the project as `$DC_PREP`.
4. After every answer and at the end, `dc claude push` saves the state (a sandbox can stop at any
   time). A hand-off written by `handoff.py` goes into the project's memory, and the next session
   on the repo, cloud or PC, starts from it.

