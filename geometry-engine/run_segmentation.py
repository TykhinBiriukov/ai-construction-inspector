"""Run Meta SAM image segmentation: python run_segmentation.py --max-frames 3."""

import argparse
import os
from pathlib import Path
from geometry_engine.session_layout import processed_session, original_directory

from dotenv import load_dotenv

from geometry_engine.sam_backend import Sam3Backend, Sam3UnavailableError
from geometry_engine.segmentation import SegmentationService, SegmentationSettings


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Segment selected session photos with the hosted Meta SAM API.")
    parser.add_argument("--session", type=Path, default=root / "data" / "processed" / "47333462")
    parser.add_argument("--output", type=Path, help="New output folder directly inside the session; defaults to SESSION/segmentation")
    parser.add_argument("--selected-frames", type=Path, help="Alternative selected_frames.json inside the session")
    parser.add_argument("--max-frames", type=int, help="Process only the first N selected frames")
    parser.add_argument("--resume", action="store_true", help="Reuse completed frames in --output and process only missing frames")
    parser.add_argument("--prompts", nargs="+", default=["wall", "window", "door"], help="One concept per argument, e.g. wall window door chair bed")
    parser.add_argument("--min-confidence", type=float, default=0.5)
    parser.add_argument("--min-mask-pixels", type=int, default=64)
    args = parser.parse_args()
    load_dotenv(root / ".env")
    api_key = os.getenv("MODEL_API_KEY", "").strip()
    if not api_key:
        parser.exit(1, "Error: Set MODEL_API_KEY in geometry-engine/.env to use Meta SAM. No requests were sent.\n")
    try:
        settings = SegmentationSettings(tuple(args.prompts), args.min_confidence, args.min_mask_pixels)
        backend = Sam3Backend(api_key=api_key)
        service = SegmentationService(backend, settings)
        print(f"Segmenting selected frames: {processed_session(args.session)}", flush=True)
        print(f"Concepts: {', '.join(settings.prompts)}", flush=True)
        def progress(done, total, frame_id, detections):
            print(f"Frame {done}/{total} ({frame_id}): {detections} detections", flush=True)
        report = service.segment(args.session, args.output, max_frames=args.max_frames,
                                 selected_frames=args.selected_frames, progress=progress, resume=args.resume)
    except Sam3UnavailableError:
        parser.exit(1, "Error: Meta SAM API request failed. Check API access, limits and connectivity. No completed result was published.\n")
    except (OSError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")
    print(f"Segmented frames: {report['processed_count']}/{report['selected_count']}; detections: {report['detection_count']}", flush=True)
    print(f"Reused: {report['reused_count']}; new: {report['newly_processed_count']}", flush=True)
    output = args.output.resolve() if args.output is not None else processed_session(args.session) / "segmentation"
    print(f"Results: {output}", flush=True)


if __name__ == "__main__":
    main()
