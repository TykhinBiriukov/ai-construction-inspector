"""Prepare a short-layout folder for upload to Supabase; no network calls."""
import argparse
import json
from pathlib import Path

from geometry_engine.capture.prepare_upload import prepare_capture_upload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', type=Path, required=True)
    parser.add_argument('--output-root', type=Path, default=Path(__file__).resolve().parents[1] / 'data/upload')
    args = parser.parse_args()
    result = prepare_capture_upload(args.session, args.output_root)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
