"""Frozen entry point: the command line (scan, export, dossier, action, entity, sources)."""
import sys

import hbnscan

if __name__ == "__main__":
    sys.exit(hbnscan.cli_main())
