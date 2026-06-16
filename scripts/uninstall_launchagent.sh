#!/usr/bin/env bash
set -euo pipefail

PLIST_NAME="com.annahua.1point3acres.checkin.plist"
TARGET_FILE="$HOME/Library/LaunchAgents/$PLIST_NAME"
SUPPORT_DIR="$HOME/Library/Application Support/1point3acres-checkin"
WRAPPER="$SUPPORT_DIR/run_daily_launchagent.sh"

if [ -f "$TARGET_FILE" ]; then
    launchctl unload "$TARGET_FILE" >/dev/null 2>&1 || true
    rm -f "$TARGET_FILE"
    echo "Successfully removed LaunchAgent: $PLIST_NAME"
else
    echo "LaunchAgent not found, nothing to do."
fi

if [ -f "$WRAPPER" ]; then
    rm -f "$WRAPPER"
    echo "Removed LaunchAgent wrapper: $WRAPPER"
fi
