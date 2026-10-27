"""Optional queue runner; normal Uvicorn startup already starts a worker."""

import argparse
import threading

from .config import Settings
from .service import InspectorService


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Process at most one due operation")
    args = parser.parse_args()
    workflow = InspectorService(Settings.from_env()).capture
    if args.once:
        print("Processed one operation." if workflow.process_next() else "No due operations.")
    else:
        try:
            workflow.run(threading.Event())
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
