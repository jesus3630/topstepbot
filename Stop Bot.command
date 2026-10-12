#!/bin/bash
# Double-click to flatten and stop. Creates the same KILL file as the red button.
set -euo pipefail
ROOT="/Users/heyzeus/topstepbot"
if [[ ! -d "$ROOT" ]]; then
  echo "Could not find $ROOT"
  echo "Press Return to close."
  read -r
  exit 1
fi
printf 'launcher stop\n' > "$ROOT/KILL"
echo "Created $ROOT/KILL"
echo "The bot will cancel working orders, flatten, and stop."
echo "Press Return to close."
read -r
