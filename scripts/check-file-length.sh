#!/usr/bin/env bash
# Rule: no code file over MAX_LINES (default 400) lines. Documentation is exempt.
# Scans tracked and untracked (not ignored) files, so generated or ignored files never count.
set -euo pipefail

MAX="${MAX_LINES:-400}"
fail=0

while IFS= read -r f; do
  [ -f "$f" ] || continue
  case "$f" in
    *.py|*.sh|*.tcss|*.yaml|*.yml|*.toml) ;;
    *) continue ;;
  esac
  lines="$(wc -l < "$f")"
  if [ "$lines" -gt "$MAX" ]; then
    echo "FAIL: $f is $lines lines (max $MAX)"
    fail=1
  fi
done < <(git ls-files --cached --others --exclude-standard)

if [ "$fail" -eq 0 ]; then
  echo "file-length OK (max $MAX lines)"
fi
exit "$fail"
