#!/usr/bin/env python3
"""
HB Briefing - design tokens

One palette table, shared by the desktop GUI and the web app, so a colour is defined once
and the two front ends cannot drift apart. The terminal interface uses the reader's own
terminal palette and deliberately does not participate.

`classic` is the product identity: Honey Badger Classic, muted light, the palette from the
design directive. It is the default for new installations. Every other theme predates it and
is kept so an existing user's explicit choice keeps working; nothing rewrites a saved theme.

Values are plain hex strings so they can be dropped into Tk options and CSS variables alike.
Contrast was checked against the WCAG 2.2 AA thresholds for body text, and severity is never
communicated by colour alone anywhere in the application.
"""
from __future__ import annotations

# The authoritative roles for the house style. THEMES["classic"] is built from this, so the
# directive's table and the implementation cannot disagree.
PALETTE = {
    "workspace": "#F5F3EB",
    "background": "#EAE8E1",
    "panel": "#E1E3DE",
    "toolbar": "#DDDCD4",
    "header": "#536B79",
    "header_text": "#F5F4EE",
    "selection": "#C4D1D5",
    "text": "#354650",
    "text_muted": "#59656A",
    "border": "#B6BCB8",
    "brand": "#A88B55",
    "success": "#66856C",
    "warning": "#A88B55",
    "error": "#A45D58",
    "information": "#536B79",
}

THEMES = {
    "classic": {
        "bg_main": PALETTE["workspace"], "bg_panel": PALETTE["panel"],
        "bg_accent": PALETTE["toolbar"], "bg_input": PALETTE["background"],
        "bg_select": PALETTE["selection"], "log_fg": PALETTE["text"],
        "fg_text": PALETTE["text"], "fg_muted": PALETTE["text_muted"],
        "accent": PALETTE["brand"], "accent_hover": "#8C7244",
        "green": PALETTE["success"], "red": PALETTE["error"],
        "header": PALETTE["header"], "header_text": PALETTE["header_text"],
        "border": PALETTE["border"], "line": PALETTE["border"],
        "line_soft": "#CFD3CC", "hover": PALETTE["selection"],
        "info": PALETTE["information"], "warning": PALETTE["warning"],
        "blue": "#3F5F76", "violet": "#6F5A86",
    },
    "dark": {
        "bg_main": "#12161f", "bg_panel": "#1a202c", "bg_accent": "#252e3f",
        "bg_input": "#0d1117", "bg_select": "#334155", "log_fg": "#cbd5e1",
        "fg_text": "#e2e8f0", "fg_muted": "#94a3b8",
        "accent": "#f59e0b", "accent_hover": "#d97706",
        "green": "#10b981", "red": "#ef4444",
        "header": "#252e3f", "header_text": "#e2e8f0",
        "border": "#253044", "line": "#253044", "line_soft": "#1b2331",
        "hover": "#1a2333", "info": "#58a6ff", "warning": "#f59e0b",
        "blue": "#58a6ff", "violet": "#a371f7",
    },
    "light": {
        "bg_main": "#eef2f6", "bg_panel": "#ffffff", "bg_accent": "#dfe6ee",
        "bg_input": "#f8fafc", "bg_select": "#c7d3e0", "log_fg": "#1e293b",
        "fg_text": "#0f172a", "fg_muted": "#475569",
        "accent": "#b45309", "accent_hover": "#92400e",
        "green": "#047857", "red": "#b91c1c",
        "header": "#c7d3e0", "header_text": "#0f172a",
        "border": "#c7d3e0", "line": "#c7d3e0", "line_soft": "#e3e9f0",
        "hover": "#e6edf5", "info": "#1d4ed8", "warning": "#b45309",
        "blue": "#1d4ed8", "violet": "#6d28d9",
    },
    "midnight": {
        "bg_main": "#0a0e17", "bg_panel": "#111827", "bg_accent": "#1c2740",
        "bg_input": "#070a11", "bg_select": "#2b3a5c", "log_fg": "#a5b4cf",
        "fg_text": "#e5ecf8", "fg_muted": "#7b8aa8",
        "accent": "#60a5fa", "accent_hover": "#3b82f6",
        "green": "#22c55e", "red": "#f87171",
        "header": "#1c2740", "header_text": "#e5ecf8",
        "border": "#1c2740", "line": "#1c2740", "line_soft": "#151d2e",
        "hover": "#131c2c", "info": "#93c5fd", "warning": "#60a5fa",
        "blue": "#93c5fd", "violet": "#c4b5fd",
    },
    "solarized": {
        "bg_main": "#fdf6e3", "bg_panel": "#eee8d5", "bg_accent": "#dfd9c3",
        "bg_input": "#fffbf0", "bg_select": "#cfc9b3", "log_fg": "#586e75",
        "fg_text": "#073642", "fg_muted": "#657b83",
        "accent": "#b58900", "accent_hover": "#8f6c00",
        "green": "#859900", "red": "#dc322f",
        "header": "#dfd9c3", "header_text": "#073642",
        "border": "#d8d2bd", "line": "#d8d2bd", "line_soft": "#e7e0cc",
        "hover": "#e9e2cd", "info": "#268bd2", "warning": "#b58900",
        "blue": "#268bd2", "violet": "#6c71c4",
    },
    "contrast": {
        "bg_main": "#000000", "bg_panel": "#0b0b0b", "bg_accent": "#1f1f1f",
        "bg_input": "#000000", "bg_select": "#3a3a3a", "log_fg": "#ffffff",
        "fg_text": "#ffffff", "fg_muted": "#d0d0d0",
        "accent": "#ffd400", "accent_hover": "#ffb700",
        "green": "#00ff88", "red": "#ff4d4d",
        "header": "#0b0b0b", "header_text": "#ffffff",
        "border": "#5a5a5a", "line": "#5a5a5a", "line_soft": "#2e2e2e",
        "hover": "#1a1a1a", "info": "#7cc4ff", "warning": "#ffd400",
        "blue": "#7cc4ff", "violet": "#d7a6ff",
    },
}

THEME_NAMES = tuple(THEMES)
DEFAULT_THEME = "classic"

# GUI palette name -> CSS custom property, so both front ends read the same tokens.
WEB_VAR_NAMES = (
    ("bg_main", "--bg"),
    ("bg_panel", "--panel"),
    ("line", "--line"),
    ("line_soft", "--line-soft"),
    ("hover", "--hover"),
    ("bg_input", "--bg-input"),
    ("fg_text", "--ink"),
    ("fg_muted", "--muted"),
    ("accent", "--amber"),
    ("green", "--green"),
    ("red", "--red"),
    ("blue", "--blue"),
    ("violet", "--violet"),
)


def palette(name):
    """The palette for `name`, falling back to the default rather than raising.

    A stale `theme` value in an old settings file must never stop the application from
    starting; it simply shows the house style.
    """
    return dict(THEMES.get(name, THEMES[DEFAULT_THEME]))


def web_vars(name):
    """The CSS custom properties for one theme, as a declaration list."""
    theme = palette(name)
    return " ".join(f"{var}:{theme[key]};" for key, var in WEB_VAR_NAMES)
