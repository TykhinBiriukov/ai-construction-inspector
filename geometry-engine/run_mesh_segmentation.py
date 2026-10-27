"""Label the prepared mesh using saved SAM masks: python run_mesh_segmentation.py."""

import argparse
from pathlib import Path

from geometry_engine.mask_fusion import FusionSettings
from geometry_engine.mesh_segmentation import MeshSegmentationService


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Project saved SAM masks onto the prepared mesh and fuse observations from multiple views.")
    parser.add_argument("--session", type=Path, default=root / "data" / "47333462")
    parser.add_argument("--output", type=Path, help="New folder directly inside the session; defaults to SESSION/mesh_segmentation")
    parser.add_argument("--projection-dir", type=Path, help="New or reusable projection folder directly inside the session; defaults to SESSION/mask_projection")
    parser.add_argument("--max-frames", type=int, help="Use only the first N already segmented frames")
    parser.add_argument("--min-views", type=int, default=2, help="Minimum independent views supporting each mesh face (default: 2)")
    parser.add_argument("--min-class-purity", type=float, default=0.65, help="Minimum winning share of class votes (default: 0.65)")
    args = parser.parse_args()
    try:
        settings = FusionSettings(min_views=args.min_views, min_class_purity=args.min_class_purity)
        service = MeshSegmentationService(fusion_settings=settings)
        print(f"Labeling mesh from saved masks: {args.session.resolve()}", flush=True)

        def progress(done, total, frame_id, supported_observations):
            print(f"Frame {done}/{total} ({frame_id}): {supported_observations} supported masks", flush=True)

        report = service.segment(args.session, args.output, projection_dir=args.projection_dir,
                                 max_frames=args.max_frames, progress=progress)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")
    print(f"Processed frames: {report['processed_frames']}; labeled faces: {report['labeled_faces']}/{report['total_faces']}", flush=True)
    print(f"Candidate objects: {report['candidate_objects']}", flush=True)
    output = args.output.resolve() if args.output is not None else args.session.resolve() / "mesh_segmentation"
    print(f"Results: {output}", flush=True)


if __name__ == "__main__":
    main()
