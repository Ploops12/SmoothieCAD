#!/usr/bin/env python3
"""Create a Blender-installable ZIP containing addon sources and its native module."""

from __future__ import annotations

import argparse
from pathlib import Path
import zipfile


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--addon-dir", required=True, type=Path)
    parser.add_argument("--native-module", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    if not (args.addon_dir / "__init__.py").is_file():
        parser.error(f"addon entry point not found under {args.addon_dir}")
    if not args.native_module.is_file():
        parser.error(f"native extension not found: {args.native_module}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(args.addon_dir.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                archive.write(path, Path("blendercad") / path.relative_to(args.addon_dir))
        archive.write(args.native_module, Path("blendercad") / args.native_module.name)


if __name__ == "__main__":
    main()
