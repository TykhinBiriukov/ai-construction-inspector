"""Reconstruct a local raw capture: python run_reconstruction.py --session data/ID."""
import argparse
from pathlib import Path
from geometry_engine.session_layout import processed_session, original_directory

from geometry_engine.reconstruction import ReconstructionService, ReconstructionSettings


def main():
    parser=argparse.ArgumentParser(description='Build a colored point cloud and mesh from raw RGB-D frames using Open3D (CPU).')
    parser.add_argument('--session',type=Path,required=True,help='Session directory containing original/')
    parser.add_argument('--output',type=Path,help='New output folder inside the session; default: SESSION/reconstruction')
    parser.add_argument('--voxel-size',type=float,default=.02,help='Voxel size in meters (default: 0.02)')
    parser.add_argument('--sdf-trunc',type=float,default=.08,help='TSDF truncation in meters (default: 0.08)')
    parser.add_argument('--max-depth',type=float,default=5.)
    parser.add_argument('--min-confidence',type=int,choices=(0,1,2),default=1)
    parser.add_argument('--exclude-frame',action='append',default=[],help='Frame UUID to exclude; repeat as needed')
    parser.add_argument('--keep-conflicting-frames',action='store_true',help='Report pose/depth conflicts without automatic exclusion')
    parser.add_argument('--no-preview',action='store_true')
    args=parser.parse_args()
    try:
        settings=ReconstructionSettings(voxel_size_m=args.voxel_size,sdf_trunc_m=args.sdf_trunc,
            max_depth_m=args.max_depth,min_confidence=args.min_confidence,
            excluded_frame_ids=tuple(args.exclude_frame),reject_conflicting_frames=not args.keep_conflicting_frames,
            create_preview=not args.no_preview)
        report=ReconstructionService(settings).reconstruct(args.session,args.output,progress=lambda text:print(text,flush=True))
    except (OSError,ValueError,RuntimeError) as error:
        parser.exit(1,f'Error: {error}\n')
    print(f"Done: {report['integrated_frames']}/{report['total_frames']} frames, {report['point_count']} points, {report['triangle_count']} triangles.")
    print(f"Results: {(args.output or processed_session(args.session)/'reconstruction').resolve()}")


if __name__=='__main__':main()
