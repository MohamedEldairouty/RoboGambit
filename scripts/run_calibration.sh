#!/usr/bin/env bash
# Launch board calibration. Usage: ./scripts/run_calibration.sh [--cam N] [--rotate DEG]
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."
python -m vision.calibrate "$@"
