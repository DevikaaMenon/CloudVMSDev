#!/usr/bin/env bash
# =====================================================================
#  Gatehouse Cloud VMS - prerequisite checker / installer (Linux, macOS)
#
#  ./run_vms.sh calls this automatically on every launch.
#  Every item is CHECKED first and installed ONLY if it is missing:
#    1. Python 3.10 - 3.12 (+ venv support)   apt / dnf / brew
#    2. Google Chrome or Chromium             (the dashboard opens in it)
#    3. Node.js                               (only if frontend/dist is missing)
#    4. Project virtual environment .venv
#    5. Python packages, model weights, demo video, .env, web build
#       (scripts/check_setup.py - same check-then-install rule)
#  Exit code 0 = ready, 1 = something required could not be set up.
# =====================================================================
set -uo pipefail
cd "$(dirname "$0")"

ok()   { printf '  [ OK ]      %s\n' "$*"; }
todo() { printf '  [INSTALL]   %s\n' "$*"; }
warn() { printf '  [WARNING]   %s\n' "$*"; }
err()  { printf '  [ERROR]     %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

OS=$(uname -s)
SUDO=""
if [ "$(id -u)" -ne 0 ] && have sudo; then SUDO="sudo"; fi
PM=""
if [ "$OS" = "Darwin" ]; then have brew && PM=brew
elif have apt-get; then PM=apt
elif have dnf; then PM=dnf
fi
APT_UPDATED=0
pkg_install() {  # pkg_install <apt names> | <dnf names> | <brew names>
  local apt_pkgs=$1 dnf_pkgs=$2 brew_pkgs=$3
  case $PM in
    apt) if [ $APT_UPDATED = 0 ]; then $SUDO apt-get update -qq || true; APT_UPDATED=1; fi
         [ -n "$apt_pkgs" ] && DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y -qq $apt_pkgs ;;
    dnf) [ -n "$dnf_pkgs" ] && $SUDO dnf install -y -q $dnf_pkgs ;;
    brew) [ -n "$brew_pkgs" ] && brew install $brew_pkgs ;;
    *) return 1 ;;
  esac
}

echo
echo "================= Checking prerequisites ================="

# ------------------------------------------------------------------ 0. sanity
if [ ! -f backend/requirements.txt ] || [ ! -f scripts/check_setup.py ]; then
  err "Project files are missing. Extract the WHOLE zip first, then run ./run_vms.sh inside it."
  exit 1
fi
ok "Project folder complete ($OS)"

# ------------------------------------------------------------------ 1. Python
py_ok() { "$1" -c 'import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,12) else 1)' >/dev/null 2>&1; }
find_python() {
  BASEPY=""
  for c in python3.12 python3.11 python3.10 python3 python; do
    if have "$c" && py_ok "$c"; then BASEPY=$(command -v "$c"); return 0; fi
  done
  for c in /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12; do
    if [ -x "$c" ] && py_ok "$c"; then BASEPY=$c; return 0; fi
  done
  return 1
}

VENV_OK=0
if [ -x .venv/bin/python ] && py_ok .venv/bin/python && .venv/bin/python -m pip --version >/dev/null 2>&1; then
  VENV_OK=1
  ok "Python environment .venv (already set up)"
elif find_python; then
  ok "Python found: $BASEPY ($("$BASEPY" --version 2>&1))"
else
  todo "Python 3.12 (no Python 3.10 - 3.12 found)"
  pkg_install "python3.12 python3.12-venv" "python3.12" "python@3.12" || \
    pkg_install "python3 python3-venv" "python3" "" || true
  if find_python; then ok "Python installed: $BASEPY"
  else
    err "Python 3.10 - 3.12 could not be installed automatically. Install Python 3.12 and run again."
    exit 1
  fi
fi

# ------------------------------------------------------------------ 2. Chrome / Chromium
find_chrome() {
  CHROME=""
  if [ "$OS" = "Darwin" ]; then
    for a in "/Applications/Google Chrome.app" "$HOME/Applications/Google Chrome.app" "/Applications/Chromium.app"; do
      [ -d "$a" ] && { CHROME=$a; return 0; }
    done
    return 1
  fi
  for c in google-chrome google-chrome-stable chromium chromium-browser; do
    have "$c" && { CHROME=$(command -v "$c"); return 0; }
  done
  return 1
}
if find_chrome; then
  ok "Chrome ($CHROME)"
else
  todo "Google Chrome (the dashboard opens in a Chrome tab)"
  case $PM in
    brew) brew install --cask google-chrome || true ;;
    apt)  if [ "$(uname -m)" = "x86_64" ]; then
            tmp=$(mktemp -d); curl -fsSL -o "$tmp/chrome.deb" https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
              && pkg_install "$tmp/chrome.deb" "" "" || true; rm -rf "$tmp"
          fi
          find_chrome || pkg_install "chromium" "" "" || pkg_install "chromium-browser" "" "" || true ;;
    dnf)  $SUDO dnf install -y -q https://dl.google.com/linux/direct/google-chrome-stable_current_x86_64.rpm || \
            pkg_install "" "chromium" "" || true ;;
  esac
  if find_chrome; then ok "Chrome installed ($CHROME)"
  else warn "Chrome could not be installed - the dashboard will open in your default browser."; fi
fi

# ------------------------------------------------------------------ 3. Node.js (only if needed)
if [ -f frontend/dist/index.html ]; then
  ok "Node.js not needed (web interface is pre-built)"
elif have npm && node -e 'process.exit(parseInt(process.versions.node) >= 18 ? 0 : 1)' 2>/dev/null; then
  ok "Node.js $(node --version)"
else
  todo "Node.js (needed to build the web interface)"
  pkg_install "nodejs npm" "nodejs npm" "node" || true
  if have npm; then ok "Node.js installed"; else err "Node.js could not be installed. Install Node 18+ and run again."; exit 1; fi
fi

# ------------------------------------------------------------------ 4. virtual environment
if [ $VENV_OK = 0 ]; then
  [ -d .venv ] && { todo "Re-creating the broken project environment .venv"; rm -rf .venv; } || todo "Project environment .venv"
  if ! "$BASEPY" -m venv .venv >/dev/null 2>&1; then
    # Debian/Ubuntu ship venv support separately
    rm -rf .venv
    ver=$("$BASEPY" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
    pkg_install "python$ver-venv" "" "" || pkg_install "python3-venv" "" "" || true
    "$BASEPY" -m venv .venv || { err "Could not create the virtual environment with $BASEPY"; exit 1; }
  fi
  ok "Project environment .venv created"
fi

# ------------------------------------------------------------------ 5. packages, models, data
.venv/bin/python scripts/check_setup.py || { echo; err "Setup is not complete - read the messages above and run again."; exit 1; }

echo "================= All prerequisites ready ================="
echo
exit 0
