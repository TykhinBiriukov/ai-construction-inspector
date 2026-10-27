"""Run session validation, mesh preprocessing and frame selection locally."""

import argparse
from pathlib import Path

from geometry_engine.preprocessing import preprocess_mesh
from geometry_engine.frame_filter import FrameFilterService, FrameFilterSettings
from geometry_engine.validation import SessionValidationService, session_file


def run_session(directory, output=None, *, frames_only=False, frame_settings=None, frames_output=None):
    root = Path(directory).resolve()
    if not root.is_dir():
        raise ValueError(f"Session directory does not exist: {root}")
    frame_filter = FrameFilterService(frame_settings)
    if frames_only:
        print(f"Selecting frames: {root}", flush=True)
        report = frame_filter.filter(root, frames_output)
        print(f"Selected frames: {report['selected_count']}/{report['total_frames']}", flush=True)
        return {"selected_frames": report}
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
    print("Selecting frames...", flush=True)
    selected = frame_filter.filter(root, frames_output)
    print(f"Selected frames: {selected['selected_count']}/{selected['total_frames']}", flush=True)
    return {"validation": validation, "preprocessing": preprocessing, "selected_frames": selected}


def main():
    parser = argparse.ArgumentParser(description="Validate a session, preprocess its mesh and select frames for SAM.")
    parser.add_argument("--session", type=Path, default=Path(__file__).resolve().parent / "data" / "47333462")
    parser.add_argument("--output", type=Path, help="New output folder; defaults to SESSION/mesh_preprocessing")
    parser.add_argument("--frames-only", action="store_true", help="Run only frame selection on an existing session")
    parser.add_argument("--frames-output", type=Path, help="New output folder inside session; defaults to SESSION/selected_frames")
    parser.add_argument("--translation-m", type=float, default=0.30)
    parser.add_argument("--view-angle-deg", type=float, default=15.0)
    parser.add_argument("--min-sharpness", type=float, default=20.0)
    args = parser.parse_args()
    try:
        settings = FrameFilterSettings(min_translation_m=args.translation_m, min_view_angle_deg=args.view_angle_deg,
                                       min_sharpness=args.min_sharpness)
        run_session(args.session, args.output, frames_only=args.frames_only,
                    frame_settings=settings, frames_output=args.frames_output)
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
