#!/usr/bin/env python3
"""Compatibility entry point; new runs use processor/p2_localizer.py."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from processor.p2_localizer import *  # noqa: F401,F403
from processor.p2_localizer import main


if __name__ == "__main__":
    main()
