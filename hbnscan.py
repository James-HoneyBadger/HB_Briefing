#!/usr/bin/env python3
"""Compatibility entry point; the implementation lives in src/hbnscan.py."""
import importlib.util
from pathlib import Path
import sys

_source = Path(__file__).resolve().parent / "src" / "hbnscan.py"
sys.path.insert(0, str(_source.parent))
if __name__ == "__main__":
    from hbnscan import cli_main
    sys.exit(cli_main())
else:
    _spec = importlib.util.spec_from_file_location(__name__, _source)
    _module = importlib.util.module_from_spec(_spec)
    sys.modules[__name__] = _module
    _spec.loader.exec_module(_module)
