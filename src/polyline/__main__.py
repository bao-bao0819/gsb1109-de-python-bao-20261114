"""Command-line entry point: ``python -m polyline A B``."""
from .feasibility import main

if __name__ == "__main__":
    raise SystemExit(main())
