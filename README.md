<div align="center">

# devcloud

**One portable CLI that keeps projects working identically on every machine.**

[![ci](https://github.com/Trick230/devcloud/actions/workflows/ci.yml/badge.svg)](https://github.com/Trick230/devcloud/actions/workflows/ci.yml)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-0078d4)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

[Installation](#installation) · [Usage](#usage) · [Security](#security) · [Issues](https://github.com/Trick230/devcloud/issues)

</div>

`dc` gives Windows and Linux PCs, cloud VMs, Claude Code web sessions and GitHub Actions
runners the same secrets, large files, agent configuration, and CPU and GPU jobs, with nothing
installed outside its own folder.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Usage](#usage)
- [Claude Code web environments](#claude-code-web-environments)
- [Security](#security)
- [Repository layout](#repository-layout)
- [Development](#development)
- [License](#license)

## Features

| Area | What it does |
|---|---|
| **Secrets** | Each project's `.env` is age-encrypted into a private state repo (`<owner>/devcloud-state`). Lines after `# dc: local` stay on the machine. |
| **Large files** | Copyrighted or large files live in `<Google Drive>/Projects/<project>/`, tracked by a sha256 manifest, never in git. |
| **Agent setup** | `~/.claude` configuration, per-project memory and the projects' local agent files are synchronised between machines. |
| **Sessions** | Hooks bring a web session's pushed `claude/*` branch into the PC's branch, clean up merged ones, and warn when another session is active on the same project. |
| **Accounts** | Switches between saved Claude Code accounts without a browser sign-in; on a rate limit, a desktop notification names the next account. |
| **CPU jobs** | `dc run` runs a command on GitHub Actions in the project's repo (up to 6 h per job, parallel with `--each`). |
| **GPU jobs** | `dc gpu` runs a project's script on a free cloud GPU: Modal, then Kaggle, then Colab (manual). |
| **Housekeeping** | Successful `dc run` jobs are removed from the Actions history; `dc gc` trims the rest. |

Every dependency (uv, Python, age, gh, rclone) is pinned by sha256 and downloaded into `.dc/`.
Nothing is installed system-wide and nothing is read from `PATH` except `git` and `bash`.

## Requirements

- Windows 10/11 or Linux, x64 or ARM64
- `git`, and `bash` (Git Bash on Windows)
- A GitHub account; optionally a Google account (Drive), Modal and Kaggle accounts (GPU)

## Installation

```sh
git clone https://github.com/<owner>/devcloud && cd devcloud
./dc setup          # Windows: .\dc setup
```

`dc setup` fetches the tools, signs in to GitHub, clones the state repo and sets up the age key.
The first machine creates the key and the private state repo. Every other machine needs the key
once: run `dc key` on a machine that has it and put the value in `DC_AGE_KEY` (or in
`.dc/age-key.txt`) before `dc setup`.

Check the result with `dc check`:

```text
platform : windows-x64
tools    : age: yes, gh: yes, rclone: yes
github   : token yes, expires 2027-02-24
state    : .../devcloud/.dc/state
age key  : yes
drive    : yes
```

## Usage

`dc help` lists every command and option. The common ones:

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
3. The project's `.claude/web-setup.sh`, if any, runs in the background (log in
   `$TMPDIR/dc-web-setup-<project>.log`). With `--prep`, heavy downloads are pre-built into the
   cached image under `~/dc-prep/<repo>`, exposed to the project as `$DC_PREP`.
4. After every answer and at the end, `dc claude push` saves the state (a sandbox can stop at any
   time). A hand-off written by `handoff.py` goes into the project's memory, and the next session
   on the repo, cloud or PC, starts from it.

## Security

- The age key is the only long-lived secret on a machine; everything in the state repo is
  encrypted to it. `dc key rotate` replaces it.
- The GitHub token should be fine-grained and limited to the repos above. `dc check` shows its
  expiry, and sessions start with a warning from 14 days before it expires.
- `DC_ENV` holds only the groups the project names in `DC_GLOBAL`. Workflows from forks never
  get repository secrets; anyone with write access to the repository does.
- Artifacts and logs of a public repository are public. Copyrighted data moves only through
  Drive (`--in`, `--out`), never `--artifact`.
- GPU tokens live in the global secrets. Modal runs with a 0 $ spend limit on the monthly credits;
  Kaggle's GPU quota is weekly.
- `dc claude` does not propagate deletions. A file changed on two machines keeps the local copy
  and leaves the other as `<name>.dc-remote`, to merge by hand.

## Repository layout

```text
dc, dc.cmd, dc.py        launchers (Linux/Git Bash, Windows) and the command-line entry point
devcloud/                core, secrets, drive, claude, session, account, run, gpu, web modules
templates/dc.yml         the per-project workflow written by dc init
.github/workflows/       ci.yml (tests on 4 platforms), run.yml (reusable job runner for dc run)
web-boot.sh              SessionStart/Stop/SessionEnd entry point in web sessions
tests/                   unit tests
.dc/                     local tools, caches, keys and the state clone (not in git)
```

## Development

```sh
./dc test                # unit tests
```

CI runs the same tests on Linux and Windows, x64 and ARM64, on every push.

## License

Released under the [MIT License](LICENSE).
