"""Extract and approximate door/window contours from a segmented mesh locally."""
import argparse
import json
from pathlib import Path
from geometry_engine.openings.mesh_boundaries import MeshBoundaryService


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('session',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args()
    result=MeshBoundaryService().run(
        args.session,args.output,resume=args.resume,progress=lambda text:print(text,flush=True))
    print(json.dumps({'status':result.get('status'),'objects':len(result.get('objects',[]))},indent=2))


if __name__=='__main__':main()
