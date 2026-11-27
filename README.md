<div align="center">

# devcloud

**One portable CLI that keeps projects working identically on every machine.**

[![ci](https://github.com/Trick230/devcloud/actions/workflows/ci.yml/badge.svg)](https://github.com/Trick230/devcloud/actions/workflows/ci.yml)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-0078d4)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

</div>

```console
$ dc run --each "./lls test pal" "./lls test tempo"
dc: run https://github.com/<owner>/LosslessSync/actions/runs/45930529415
--- run / ./lls test pal: success
  Ran 22 tests in 30.201s
  OK
  pal: PASS
--- run / ./lls test tempo: success
  Ran 22 tests in 79.941s
  OK
  tempo: PASS
dc: run succeeded (deleted from the Actions history; failed runs are kept)
```

<sub>Abridged: two test jobs run in parallel on GitHub Actions, started locally, in 2 minutes.</sub>

`dc` gives Windows and Linux PCs, cloud VMs, Claude Code web sessions and GitHub Actions
runners the same secrets, large files, agent configuration, and CPU and GPU jobs, with nothing
installed outside its own folder.

## Highlights

- **Encrypted secrets everywhere.** Each project's `.env` is age-encrypted in a private state
  repo and restored on any machine with one command.
- **Large files out of git.** Media and models live in Google Drive, tracked by a sha256
  manifest and verified on every pull.
- **Cloud CPU and GPU on demand.** `dc run` runs any command on GitHub Actions (parallel with
  `--each`); `dc gpu` runs a script on a free cloud GPU (Modal, then Kaggle).
- **Agent setup that follows you.** Claude Code configuration, memory and hand-offs sync
  between PCs and web sessions, with a warning when two sessions work on the same project.
- **Clean by default.** Successful jobs leave no Actions history; `dc gc` trims the rest.
- **Self-contained.** uv, Python, age, gh and rclone are pinned by sha256 into `.dc/`; only
  `git` and `bash` come from the system.

## Installation

```sh
git clone https://github.com/<owner>/devcloud && cd devcloud
./dc setup          # Windows: .\dc setup
```

`dc setup` fetches the tools, signs in to GitHub, clones the state repo and sets up the age
key. The first machine creates the key; every other machine gets it once (`dc key` on a machine
that has it, into `DC_AGE_KEY` or `.dc/age-key.txt`). Windows or Linux, x64 or ARM64.

```console
$ dc check
platform : windows-x64
tools    : age: yes, gh: yes, rclone: yes
github   : token yes, expires 2027-02-24
age key  : yes
drive    : yes
```

## Usage

```sh
dc secrets push | pull           # the project's .env <-> encrypted state repo
dc drive push media/             # project files -> Drive, recorded in the manifest
dc drive pull                    # everything in the manifest, verified by sha256
dc claude pull | push            # agent configuration and memory (the hooks run these)
dc init                          # add the job workflow to a project (commit and push it)
dc run -- ./lls test all         # one job on GitHub Actions
dc run --in refs:in --out out:results -- ./lls batch in out   # files in and out via Drive
dc gpu gpu/separate.json --in src.flac --out stems/           # one job on a cloud GPU
dc gc --all                      # trim the Actions history of every project
```

Every command group, accounts, and the Claude Code web environment setup:
**[docs/GUIDE.md](docs/GUIDE.md)**, or `dc help`.

## How it works

1. **State repo:** a private `<owner>/devcloud-state` holds the encrypted secrets, Drive
   manifests, agent files and session claims; every machine clones it into `.dc/state`.
2. **Hooks:** Claude Code session hooks pull the state at start, push it after every answer,
   and bring a web session's `claude/*` branch into the PC's branch.
3. **Jobs:** `dc init` adds a workflow that calls devcloud's reusable `run.yml`; `dc run`
   dispatches it on the pushed commit, with the project's secrets in one Actions secret.
4. **GPU:** a job spec (`JOB.json`) names the script and its inputs; `dc gpu` tries Modal, then
   Kaggle, then prints manual Colab steps.

## Security

- The age key is the only long-lived secret on a machine; `dc key rotate` replaces it and
  re-encrypts everything.
- The GitHub token should be fine-grained and limited to the needed repos. `dc check` shows its
  expiry, and sessions warn from 14 days before it.
- A project's Actions secret holds only the global groups it names in `DC_GLOBAL`. Workflows
  from forks never get it.
- Logs and artifacts of a public repo are public: copyrighted data moves only through Drive.
- Drive access uses the `drive.file` scope: `dc` sees only the files it uploaded itself.

## Development

```sh
./dc test                # unit tests
```

| Path | Contents |
| --- | --- |
| `dc`, `dc.cmd`, `dc.py` | launchers (Linux and Git Bash, Windows) and the entry point |
| `devcloud/` | core, secrets, drive, claude, session, account, run, gpu and web modules |
| `templates/dc.yml` | the per-project workflow written by `dc init` |
| `.github/workflows/` | `ci.yml` (tests on four platforms), `run.yml` (the reusable job runner) |
| `web-boot.sh` | session hook entry point in Claude Code web sessions |

CI runs the tests on Linux and Windows, x64 and ARM64, on every push.

## Limitations

- A GitHub Actions job runs at most 6 hours; longer work goes to a web session (`dc brief`).
- Colab has no API: the last GPU fallback is manual.
- `dc claude` does not propagate deletions; a file changed on two machines leaves the other
  copy as `<name>.dc-remote`, to merge by hand.
- A Claude Code web session reaches only the repos it was started with.

## Acknowledgements

[age](https://github.com/FiloSottile/age), [rclone](https://rclone.org),
[GitHub CLI](https://cli.github.com), [uv](https://github.com/astral-sh/uv),
[Modal](https://modal.com) and [Kaggle](https://www.kaggle.com).

## License

Released under the [MIT License](LICENSE).
