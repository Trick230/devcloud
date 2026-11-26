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
    "$D/dc" setup </dev/null >&2 || echo "dc: setup failed (is DC_GH_TOKEN in the environment's variables?)" >&2
    "$D/dc" claude pull --quiet </dev/null >&2
    [ -f "$HOOK" ] && exec sh "$HOOK" start
    cat >/dev/null ;;
  stop|end)
    [ -f "$HOOK" ] && exec sh "$HOOK" "$1"
    cat >/dev/null ;;
esac
exit 0
