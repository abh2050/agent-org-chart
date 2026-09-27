"""CLI: python -m gates [--from G2] [--through G3] [--only G4]"""

import argparse
import sys

from gates.definitions import GATES
from gates.engine import Decision, run_gates


def main() -> int:
    parser = argparse.ArgumentParser(description="Run SDLC gates in order.")
    ids = [g.id for g in GATES]
    parser.add_argument("--from", dest="start", choices=ids, help="first gate to run (previous must be GO)")
    parser.add_argument("--through", choices=ids, help="last gate to run")
    parser.add_argument("--only", choices=ids, help="run a single gate (previous must be GO)")
    args = parser.parse_args()

    start, through = (args.only, args.only) if args.only else (args.start, args.through)
    outcomes = run_gates(GATES, start=start, through=through)

    print("\n══ Gate summary ══")
    for o in outcomes:
        print(f"  {o.id:<3} {o.phase:<15} {o.decision.value}")
    return 1 if any(o.decision is Decision.NO_GO for o in outcomes) else 0


if __name__ == "__main__":
    sys.exit(main())
