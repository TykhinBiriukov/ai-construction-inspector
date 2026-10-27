"""Run local session validation followed by mesh preprocessing."""

import argparse
from pathlib import Path

from geometry_engine.preprocessing import preprocess_mesh
from geometry_engine.validation import SessionValidationService, session_file


def run_session(directory, output=None):
    root = Path(directory).resolve()
    if not root.is_dir():
        raise ValueError(f"Session directory does not exist: {root}")
    original = (root / "original").resolve()
    destination = Path(output).resolve() if output is not None else root / "mesh_preprocessing"
    if destination.is_relative_to(original):
        raise ValueError("Output must not be inside original; source data is read-only")
    print(f"Validating: {root}", flush=True)
    validation = SessionValidationService().validate(root)
    if validation["status"] == "invalid":
        raise ValueError("Session validation failed: " + "; ".join(validation["errors"]))
    frames = validation["frames"]
    print(f"Validation: {validation['status']}; usable frames: {frames['usable']}/{frames['total']}", flush=True)
    mesh = session_file(original, original, validation["mesh"]["path"])
    print(f"Preprocessing mesh: {mesh}", flush=True)
    preprocessing = preprocess_mesh(mesh, destination, unit="meter")
    print(f"Mesh: {preprocessing['status']}", flush=True)
    print(f"Results: {destination}", flush=True)
    return {"validation": validation, "preprocessing": preprocessing}


def main():
    parser = argparse.ArgumentParser(description="Validate a local session and preprocess its mesh.")
    parser.add_argument("--session", type=Path, default=Path(__file__).resolve().parent / "data" / "syntethic_data")
    parser.add_argument("--output", type=Path, help="New output folder; defaults to SESSION/mesh_preprocessing")
    args = parser.parse_args()
    try:
        run_session(args.session, args.output)
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
