#!/usr/bin/env bash
#
# The friendly way in. Sets everything up the first time it runs, then shows a
# menu. No commands to remember: run `bash start.command` and pick a number.
#
cd "$(dirname "$0")"

BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[32m'; RED=$'\033[31m'; OFF=$'\033[0m'

say()  { printf "%s\n" "$1"; }
step() { printf "\n${BOLD}%s${OFF}\n" "$1"; }
ok()   { printf "${GREEN}  ✓ %s${OFF}\n" "$1"; }
bad()  { printf "${RED}  ✗ %s${OFF}\n" "$1"; }

# --- 1. Is Python installed? ------------------------------------------------
if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1 && python -c 'import sys;exit(0 if sys.version_info>=(3,9) else 1)' 2>/dev/null; then
  PY=python
else
  bad "Python is not installed on this computer."
  say ""
  say "  Install it once, then run this again:"
  say "    1. Go to  ${BOLD}https://www.python.org/downloads/${OFF}"
  say "    2. Click the big yellow 'Download Python' button"
  say "    3. Open the downloaded file and click Continue / Agree / Install"
  say "    4. Come back here and run this again"
  say ""
  exit 1
fi

# --- 2. First-run setup -----------------------------------------------------
if [ ! -d ".venv" ]; then
  step "Setting up for the first time. This takes a minute or two."
  say "${DIM}  (Only happens once. You'll see some scrolling text - that's normal.)${OFF}"

  $PY -m venv .venv || { bad "Could not create the workspace."; exit 1; }
  ok "workspace created"

  ./.venv/bin/pip install --quiet --upgrade pip >/dev/null 2>&1
  ./.venv/bin/pip install --quiet -r requirements.txt || { bad "Could not install the bits it needs."; exit 1; }
  ok "everything installed"

  ./.venv/bin/python main.py demo >/dev/null || { bad "Could not create the sample data."; exit 1; }
  ok "sample conversations ready"
fi

PY=./.venv/bin/python

# --- 3. Menu ----------------------------------------------------------------
while true; do
  cat <<MENU

${BOLD}Buyer Bot - what would you like to do?${OFF}

  ${BOLD}1${OFF}  Talk to the bot          ${DIM}(pretend you're a buyer)${OFF}
  ${BOLD}2${OFF}  Open the dashboard       ${DIM}(see the leads and briefs it produced)${OFF}
  ${BOLD}3${OFF}  Read a sample conversation
  ${BOLD}4${OFF}  Start over with fresh data
  ${BOLD}5${OFF}  Quit

MENU
  printf "Type a number and press Enter: "
  # A failed read means input ended (Ctrl-D) - leave, don't spin forever.
  read -r choice || { printf "\n"; exit 0; }

  case "$choice" in
    1)
      step "Starting a conversation. Answer like a real buyer would."
      say "${DIM}  Tip: you can type full sentences, not just the options shown.${OFF}"
      say "${DIM}  Press Ctrl and C together to stop early.${OFF}"
      printf "\nWhat name should the bot greet you by? "
      read -r tester_name || tester_name=""
      $PY main.py chat --name "${tester_name:-Tester}" --number "+2010$(date +%H%M%S)0"
      printf "\n${DIM}That conversation is now in the dashboard - pick 2 to see the brief\nyour consultant would have received before calling this buyer.${OFF}\n"
      ;;
    2)
      step "Opening the dashboard at http://localhost:8000"
      say "${DIM}  Your browser should open by itself. If it doesn't, copy that address into it.${OFF}"
      say "${DIM}  Press Ctrl and C together here when you're done looking.${OFF}"
      ( sleep 3; command -v open >/dev/null && open http://localhost:8000 ) &
      $PY -m uvicorn src.app:app --host 127.0.0.1 --port 8000
      ;;
    3)
      step "Sample conversations"
      ls samples/transcript_*.md | nl -w2 -s'  ' | sed 's#samples/transcript_##; s#\.md##'
      printf "\nType a number: "
      read -r pick || pick=""
      file=$(ls samples/transcript_*.md | sed -n "${pick}p")
      [ -n "$file" ] && ${PAGER:-less} "$file" || bad "No sample with that number."
      ;;
    4)
      step "Clearing the test data and rebuilding the samples"
      $PY main.py demo
      ;;
    5|q|quit|exit)
      printf "\nBye. Run ${BOLD}bash start.command${OFF} any time to come back.\n\n"
      exit 0
      ;;
    *)
      bad "Please type 1, 2, 3, 4 or 5."
      ;;
  esac
done
