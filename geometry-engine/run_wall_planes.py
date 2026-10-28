"""Detect wall plane candidates: python run_wall_planes.py."""

import argparse
from pathlib import Path

from geometry_engine.wall_plane_detection import WallPlaneSettings
from geometry_engine.wall_planes import WallPlaneService


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Fit RANSAC wall planes and show translucent overlays on the mesh.")
    parser.add_argument("--session", type=Path, default=root / "data" / "47333462")
    parser.add_argument("--output", type=Path, help="New stage folder inside the session; default SESSION/wall_planes")
    parser.add_argument("--up-axis", choices=("x", "y", "z"), help="World up axis, inferred from known capture formats by default")
    parser.add_argument("--distance-m", type=float, default=.025, help="Maximum plane support distance in meters")
    parser.add_argument("--min-area-m2", type=float, default=1., help="Minimum supporting mesh surface area")
    parser.add_argument("--max-planes", type=int, default=12)
    args = parser.parse_args()
    try:
        settings = WallPlaneSettings(distance_threshold_m=args.distance_m,
                    min_support_area_m2=args.min_area_m2, max_planes=args.max_planes)
        report = WallPlaneService(settings).detect(args.session, args.output, up_axis=args.up_axis,
                                                   progress=lambda message: print(message, flush=True))
    except (OSError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")
    output = args.output.resolve() if args.output is not None else args.session.resolve() / "wall_planes"
    print(f"Found {report['plane_count']} planes; {report['wall_candidate_count']} have SAM wall support.", flush=True)
    print(f"Viewer: {output / 'viewer.html'}", flush=True)


if __name__ == "__main__":
    main()
