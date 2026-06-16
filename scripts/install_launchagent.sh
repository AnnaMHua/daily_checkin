#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAUNCHAGENT_LABEL="com.annahua.daily-checkin"
PLIST_NAME="$LAUNCHAGENT_LABEL.plist"
LEGACY_PLIST_NAME="com.annahua.1point3acres.checkin.plist"
TARGET_DIR="$HOME/Library/LaunchAgents"
TARGET_FILE="$TARGET_DIR/$PLIST_NAME"
LEGACY_TARGET_FILE="$TARGET_DIR/$LEGACY_PLIST_NAME"
SUPPORT_DIR="$HOME/Library/Application Support/daily_checkin"
APP_DIR="$SUPPORT_DIR/app"
WRAPPER="$SUPPORT_DIR/run_daily_launchagent.sh"
STDOUT_LOG="$SUPPORT_DIR/launchagent.out.log"
STDERR_LOG="$SUPPORT_DIR/launchagent.err.log"

# Default to 00:05 AM.
HOUR="${1:-0}"
MINUTE="${2:-5}"

# Unload existing LaunchAgents if any, including the pre-rename label.
launchctl unload "$TARGET_FILE" >/dev/null 2>&1 || true
launchctl unload "$LEGACY_TARGET_FILE" >/dev/null 2>&1 || true
if [ -f "$LEGACY_TARGET_FILE" ]; then
    rm -f "$LEGACY_TARGET_FILE"
fi
mkdir -p "$TARGET_DIR"
mkdir -p "$SUPPORT_DIR"
mkdir -p "$APP_DIR/scripts" "$APP_DIR/data" "$APP_DIR/logs"

cp "$ROOT/scripts/chrome_daily.py" "$APP_DIR/scripts/chrome_daily.py"
cp "$ROOT/data/question_bank.json" "$APP_DIR/data/question_bank.json"
if [ -f "$ROOT/data/local_question_bank.json" ] && [ ! -f "$APP_DIR/data/local_question_bank.json" ]; then
    cp "$ROOT/data/local_question_bank.json" "$APP_DIR/data/local_question_bank.json"
fi
if [ -f "$ROOT/.env" ]; then
    cp "$ROOT/.env" "$APP_DIR/.env"
fi

cat > "$WRAPPER" <<EOF
#!/bin/bash
set -euo pipefail

export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export TZ="America/Los_Angeles"
export LANG="en_US.UTF-8"
export LC_ALL="en_US.UTF-8"

echo "[\$(/bin/date '+%Y-%m-%d %H:%M:%S %Z')] LaunchAgent starting"
cd "$APP_DIR"
exec /usr/bin/python3 "$APP_DIR/scripts/chrome_daily.py" run --submit
EOF

chmod 755 "$WRAPPER"

cat <<EOF > "$TARGET_FILE"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LAUNCHAGENT_LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$WRAPPER</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
        <key>TZ</key>
        <string>America/Los_Angeles</string>
        <key>LANG</key>
        <string>en_US.UTF-8</string>
        <key>LC_ALL</key>
        <string>en_US.UTF-8</string>
    </dict>
    <key>WorkingDirectory</key>
    <string>$SUPPORT_DIR</string>
    <key>StandardOutPath</key>
    <string>$STDOUT_LOG</string>
    <key>StandardErrorPath</key>
    <string>$STDERR_LOG</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>$HOUR</integer>
        <key>Minute</key>
        <integer>$MINUTE</integer>
    </dict>
</dict>
</plist>
EOF

chmod 644 "$TARGET_FILE"
launchctl load "$TARGET_FILE"

echo "Successfully installed LaunchAgent: $PLIST_NAME"
echo "Scheduled time: $HOUR:$MINUTE (macOS will automatically run it on wake-up if missed during sleep)"
