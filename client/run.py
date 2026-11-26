#!/usr/bin/env python3
"""Direct script launcher: `sudo python3 client/run.py --server http://IP:8000/api/report`."""
import os
import sys

# Ensure parent directory of `client/` is on sys.path regardless of CWD
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from client.main import main  # noqa: E402

if __name__ == "__main__":
    main()

