#!/usr/bin/env python3
"""
finder.py

Small helper script to locate generated topology map files.

Usage:
    python finder.py
"""

from __future__ import annotations

import glob
import os


SEARCH_PATTERNS = [
    "generated_maps/*.html",
    "generated_maps/*.txt",
    "Static/maps/*.html",
    "**/*_map.html",
    "**/*map*.html",
]


def main() -> int:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    print("🔍 Searching for map files...")

    found = []
    for pat in SEARCH_PATTERNS:
        matches = glob.glob(os.path.join(base_dir, pat), recursive=True)
        found.extend(matches)

    # De-duplicate while keeping order
    seen = set()
    unique = []
    for p in found:
        if p not in seen and os.path.isfile(p):
            seen.add(p)
            unique.append(p)

    if not unique:
        print("❌ No map files found.")
        return 1

    print(f"✅ Found {len(unique)} file(s):")
    for p in unique:
        rel = os.path.relpath(p, base_dir)
        print(f"  - {rel}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
