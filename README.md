# devcloud

One portable CLI, `dc`, that keeps my projects working the same on every machine (Windows PC,
Linux, cloud VMs, Claude Code web sessions, GitHub Actions runners):

- **secrets**: each project's `.env`, age-encrypted in a private state repo (`<owner>/devcloud-state`)
- **Drive**: copyrighted or big files in `<Google Drive>/<project>/`, checked by sha256, never in git
- **Claude setup**: `~/.claude` and `~/.codex` config, per-project memory and the projects' local agent files
- **CPU jobs**: `dc run -- CMD` runs on GitHub Actions in the project's repo (up to 6 h per job,
  parallel with `--each`); `dc brief` writes the same job as a task for a Claude Code web session

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

Environment variables: `DC_AGE_KEY` (from `dc key`) and `GH_TOKEN` (a token with `repo` scope).
Setup script:

```sh
git clone https://github.com/<owner>/devcloud ~/devcloud && ~/devcloud/dc setup && ~/devcloud/dc claude pull --quiet
```

## Use

```sh
dc secrets push            # in a project: its .env -> state repo (lines after "# dc: local" stay)
dc secrets pull
dc secrets gh              # global + project secrets -> the repo's Actions secret DC_ENV

dc drive login             # once, in a browser
dc drive push media/       # project files -> Drive, recorded in the manifest
dc drive pull              # everything in the manifest, verified
dc drive ls refs  /  dc drive get refs/a.flac in/  /  dc drive put out/ synced

dc claude push             # end of a session
dc claude pull             # start of a session

dc init                    # adds .github/workflows/dc.yml to the project (commit and push it)
dc run -- ./lls test all
dc run --in refs:in --out out:results -- ./lls batch in out
dc run --each "./lls test pal" "./lls test tempo"
dc brief -- ./lls batch in out
```

`dc help` lists everything.

## Notes

- Artifacts and logs of a public repo are public. Anything copyrighted goes in and out through
  Drive (`--in`, `--out`), never `--artifact`.
- `DC_ENV` holds the Drive token, which can read the whole Drive. Workflows from forks never get
  repo secrets, but anyone with write access to the repo does.
- Deletions are not propagated by `dc claude`; a file changed on two machines keeps the local copy
  and leaves the other as `<name>.dc-remote`.

## License

MIT
