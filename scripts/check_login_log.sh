#!/bin/bash
# Summarize login audit log (uses python helper). Usage:
#   ./check_login_log.sh /path/to/login_audit.log
# Defaults to login_audit.log in current dir if not provided.

set -e
LOGFILE="$1"
if [ -z "$LOGFILE" ]; then
  LOGFILE="login_audit.log"
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PY_SCRIPT="$SCRIPT_DIR/check_login_log.py"

if [ ! -f "$PY_SCRIPT" ]; then
  echo "Python helper not found: $PY_SCRIPT" >&2
  exit 1
fi

python "$PY_SCRIPT" "$LOGFILE"
