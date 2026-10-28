#!/bin/sh
set -eu
cd "$(dirname "$0")"
uv run python prepare.py
exec uv run pywrangler deploy "$@"
