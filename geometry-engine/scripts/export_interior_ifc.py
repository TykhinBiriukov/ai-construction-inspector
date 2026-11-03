"""Save the interior IFC geometry displayed by an existing comparison viewer."""
import argparse
import json
from pathlib import Path

from geometry_engine.visualization.interior_ifc import export_interior_ifc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--viewer', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export_interior_ifc(args.source, args.viewer, args.output), indent=2))


if __name__ == '__main__':
    main()
