"""Command-line entry point for mesh preprocessing."""

import argparse
from pathlib import Path

from .preprocessing import preprocess_mesh


def main():
    parser = argparse.ArgumentParser(description="Prepare an existing triangle mesh in metres.")
    parser.add_argument("--mesh", type=Path, required=True, help="Input triangle PLY in metres")
    parser.add_argument("--output", type=Path, required=True, help="New output directory")
    args = parser.parse_args()
    report = preprocess_mesh(args.mesh, args.output)
    cleanup = report["cleanup"]
    print(f"Prepared: {cleanup['prepared_vertices']:,} vertices, {cleanup['prepared_faces']:,} triangles")
    print(f"Status: {report['status']}")
    print(f"Report: {args.output.resolve() / 'report.json'}")
