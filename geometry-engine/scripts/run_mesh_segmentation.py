"""Label the prepared mesh using saved SAM masks: python -m scripts.run_mesh_segmentation."""

import argparse
from pathlib import Path
from geometry_engine.capture.session_layout import processed_session, original_directory

from geometry_engine.segmentation.mask_fusion import FusionSettings
from geometry_engine.segmentation.mesh_segmentation import MeshSegmentationService
from geometry_engine.segmentation.mask_projection import ProjectionSettings


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Project saved SAM masks onto the prepared mesh and fuse observations from multiple views.")
    parser.add_argument("--session", type=Path, default=root / "data" / "processed" / "47333462")
    parser.add_argument("--output", type=Path, help="New folder directly inside the session; defaults to SESSION/mesh_segmentation")
    parser.add_argument("--projection-dir", type=Path, help="New or reusable projection folder directly inside the session; defaults to SESSION/mask_projection")
    parser.add_argument("--segmentation-dir", type=Path, help="SAM stage to project; defaults to SESSION/segmentation")
    parser.add_argument("--alignment-dir", type=Path, help="Explicit fixed-mesh camera alignment stage; does not change mesh")
    parser.add_argument("--min-depth-agreement", type=float, default=0.1, help="Minimum agreement among valid comparable mask depths")
    parser.add_argument("--max-frames", type=int, help="Use only the first N already segmented frames")
    parser.add_argument("--min-views", type=int, default=2, help="Minimum independent views supporting each mesh face (default: 2)")
    parser.add_argument("--min-class-purity", type=float, default=0.65, help="Minimum winning share of class votes (default: 0.65)")
    args = parser.parse_args()
    try:
        settings = FusionSettings(min_views=args.min_views, min_class_purity=args.min_class_purity)
        service = MeshSegmentationService(fusion_settings=settings, projection_settings=ProjectionSettings(
            min_comparable_depth_agreement=args.min_depth_agreement))
        print(f"Labeling mesh from saved masks: {processed_session(args.session)}", flush=True)

        def progress(done, total, frame_id, supported_observations):
            print(f"Frame {done}/{total} ({frame_id}): {supported_observations} supported masks", flush=True)

        report = service.segment(args.session, args.output, projection_dir=args.projection_dir,
                                 segmentation_dir=args.segmentation_dir, alignment_dir=args.alignment_dir,
                                 max_frames=args.max_frames, progress=progress)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")
    print(f"Processed frames: {report['processed_frames']}; labeled faces: {report['labeled_faces']}/{report['total_faces']}", flush=True)
    print(f"Candidate objects: {report['candidate_objects']}", flush=True)
    output = args.output.resolve() if args.output is not None else processed_session(args.session) / "mesh_segmentation"
    print(f"Results: {output}", flush=True)


if __name__ == "__main__":
    main()
