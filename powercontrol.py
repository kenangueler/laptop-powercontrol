#!/usr/bin/env python3
"""Launcher for a source checkout or the copy installed by install.sh. See powerctl/cli.py."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from powerctl.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
