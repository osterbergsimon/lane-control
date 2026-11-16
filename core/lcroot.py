"""Find the lane-control checkout: the nearest directory, from a file or folder upwards, that holds the marker file."""
from pathlib import Path

MARKER = ".lane-control-root"


def find_root(start: Path) -> Path:
    """The lane-control root above `start` (symlinks resolved); FileNotFoundError when there is none."""
    p = Path(start).resolve()
    for d in (p, *p.parents):
        if (d / MARKER).is_file():
            return d
    raise FileNotFoundError(f"lane-control root not found above {p} (no {MARKER})")
