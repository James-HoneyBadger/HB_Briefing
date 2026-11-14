"""Frozen entry point: the browser front end (also the PWA host for iPadOS and ChromeOS)."""
import sys

import hbnweb

if __name__ == "__main__":
    sys.exit(hbnweb.main())
