#!/bin/bash
# Double-click to open the command center and the armed bot.
# Stay at this Mac until 10:30 CT. This does not schedule anything.
set -euo pipefail
ROOT="/Users/heyzeus/topstepbot"
if [[ ! -d "$ROOT" ]]; then
  echo "Could not find $ROOT"
  echo "Press Return to close."
  read -r
  exit 1
fi
osascript <<EOF
tell application "Terminal"
  activate
  do script "cd '$ROOT' && git pull && source .venv/bin/activate && python -m topstepbot dashboard"
  do script "cd '$ROOT' && source .venv/bin/activate && python -m topstepbot practice --arm"
end tell
EOF
echo "Two windows should be open. Stay at this Mac until 10:30 CT."
echo "The bot is armed because you started it. Use Stop Bot if you need to leave."
