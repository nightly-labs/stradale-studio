#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
mkdir -p .build
if [ ! -x .build/recognize ] || [ native/recognize.swift -nt .build/recognize ]; then
  swiftc -O native/recognize.swift -o .build/recognize
fi
export STRADALE_OCR="$PWD/.build/recognize"
exec uv run python launcher.py "$@"
