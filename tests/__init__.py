"""So that `python3 -m unittest tests.test_x` finds testhome, which each test module imports first (`discover -s tests`
does not load this file: it puts tests/ on sys.path itself)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
