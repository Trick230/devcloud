#!/bin/sh
# Claude Code web: hooks that `dc setup --web` (the environment's setup script) puts into
# ~/.claude/settings.json. The setup script cannot read the environment's variables
# (DC_GH_TOKEN, DC_AGE_KEY), the session can: so the state repo, the key and `dc claude pull`
# happen here, at SessionStart. The pull brings the stored settings and ~/.claude/tools/dc-hook.sh,
# which then does the rest (stop/end too).
D="$(cd "$(dirname "$0")" && pwd)"
HOOK="$HOME/.claude/tools/dc-hook.sh"
case "$1" in
  start)
    # the environment's cached image keeps the devcloud of its setup run: update it, but only
    # to a commit a PC holding the age key vouched for (the GitHub token alone can push to
    # devcloud); this copy checks it before any new code runs, and --ff-only refuses a rollback
    # (git writes new files, so this running script keeps reading its old copy)
    git -C "$D" fetch -q origin main </dev/null >/dev/null 2>&1
    "$D/dc" setup </dev/null >&2 || echo "dc: setup failed (is DC_GH_TOKEN in the environment's variables?)" >&2
    old=$(git -C "$D" rev-parse HEAD 2>/dev/null); new=$(git -C "$D" rev-parse FETCH_HEAD 2>/dev/null)
    if [ -n "$new" ] && [ "$new" != "$old" ]; then
      if "$D/dc" vouched devcloud "$new" </dev/null >&2; then
        git -C "$D" merge -q --ff-only "$new" </dev/null >&2 2>&1 && "$D/dc" setup </dev/null >&2
      else
        echo "dc: devcloud $new is not vouched for by a PC holding the age key: staying on $old" >&2
      fi
    fi
    "$D/dc" claude pull --quiet </dev/null >&2
    [ -f "$HOOK" ] && exec sh "$HOOK" start
    cat >/dev/null ;;
  stop|end)
    [ -f "$HOOK" ] && exec sh "$HOOK" "$1"
    cat >/dev/null ;;
esac
exit 0
