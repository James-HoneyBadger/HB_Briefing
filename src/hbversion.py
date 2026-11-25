"""The one place the version is written down.

Kept free of imports so the packaging scripts can read it without importing the engine
(which loads `.env` and touches the filesystem).
"""

VERSION = "1.0.0"
APP_NAME = "HB Briefing"
