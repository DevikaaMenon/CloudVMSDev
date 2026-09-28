#!/usr/bin/env bash
# Kept for compatibility - checks prerequisites and installs only what is missing.
# To set up AND launch in one step use ./run_vms.sh
exec bash "$(dirname "$0")/install_prerequisites.sh" "$@"
