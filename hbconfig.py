#!/usr/bin/env python3
"""
HB Briefing - configuration workspace

Structured, validated access to every scan input, so a front end can change configuration
without anyone opening a text file by hand:

- `feeds.txt`      sources
- `topics.txt`     standing subjects
- `watchlist.txt`  targets (pinned names)
- `profiles/*.json` relevance criteria (categories, signals, prompts, shipped topics/targets)
- `.env`           AI provider, scan budgets, notification transport
- `settings.json`  scan shape, schedule, filters (already handled by `hbnscan.load/save_settings`)

**Nothing here rewrites documentation.** A configuration file is parsed into a line list
(`Document`); only the lines that are entries are ever re-rendered. Comment blocks, headers and
blank lines are preserved byte for byte, because the shipped files are heavily commented and a
visual editor must not eat the instructions. `Document.text()` on an unmodified document returns
the original text.

Validation raises `ValueError` with a message a person can act on. `diagnose()` collects
everything that looks wrong across all of those inputs in one place.
"""
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from urllib.parse import urlparse

import hbnscan

SEVERITY_LEVELS = ("high", "medium", "low")
SMTP_SECURITY = ("starttls", "ssl", "none")

# Profile fields that are single strings, in the order the editor shows them.
PROFILE_TEXT_FIELDS = (
    ("name", "Name"),
    ("focus", "Core focus"),
    ("secondary", "Secondary (context only)"),
    ("briefing_interest", "Briefing interest"),
    ("deep_always_relevant", "Always relevant on a full read"),
    ("severity_high", "High severity means"),
    ("severity_medium", "Medium severity means"),
    ("severity_low", "Low severity means"),
    ("excluded", "Explicitly excluded"),
)

PROFILE_LIST_FIELDS = (
    ("categories", "Categories"),
    ("core_categories", "Core categories"),
    ("strong_signals", "Strong signals"),
    ("weak_signals", "Weak signals"),
    ("noise_title_signals", "Noise titles"),
    ("enforcement_signals", "Enforcement signals"),
    ("trusted_source_hosts", "Trusted source hosts"),
    ("aggregator_hosts", "Aggregator hosts"),
)


# --------------------------------------------------------------------------- documents


@dataclass
class Line:
    """One line of a configuration file, with parsed entry data when it is an entry."""

    kind: str                     # "entry" | "comment" | "blank"
    text: str
    data: dict = field(default_factory=dict)
    error: str = ""               # set when the entry could not be parsed
    dirty: bool = False           # re-render only lines the editor actually changed


class Document:
    """A configuration file as ordered lines, so comments survive every edit.

    `parse_entry(text, lineno)` returns `(data, error)`. An entry that fails to parse keeps
    its original text: the editor reports it and never silently deletes a line it did not
    understand.
    """

    def __init__(self, path, parse_entry, render_entry):
        self.path = Path(path)
        self._parse_entry = parse_entry
        self._render_entry = render_entry
        self.lines = []
        self.load()

    def load(self):
        self.lines = []
        if not self.path.exists():
            return self
        for lineno, raw in enumerate(
                self.path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = raw.strip()
            if not stripped:
                self.lines.append(Line("blank", raw))
            elif stripped.startswith("#"):
                self.lines.append(Line("comment", raw))
            else:
                data, error = self._parse_entry(stripped, lineno)
                self.lines.append(Line("entry", raw, data or {}, error))
        return self

    def entries(self):
        """`(ordinal, data, error)` for every entry, in file order.

        The ordinal counts entries only — comments and blank lines are invisible to editors,
        so a table row maps straight onto it.
        """
        ordinal = 0
        out = []
        for line in self.lines:
            if line.kind == "entry":
                out.append((ordinal, line.data, line.error))
                ordinal += 1
        return out

    def _line_for(self, ordinal):
        """The line object for an entry ordinal; raises a clear error when it is missing."""
        if ordinal < 0:
            raise ValueError("there is no entry at that position")
        seen = 0
        for line in self.lines:
            if line.kind == "entry":
                if seen == ordinal:
                    return line
                seen += 1
        raise ValueError("there is no entry at that position")

    def entry_values(self):
        return [line.text for line in self.lines if line.kind == "entry"]

    def append(self, data):
        self.lines.append(Line("entry", self._render_entry(data), dict(data), dirty=True))
        return len(self.entries()) - 1

    def update(self, ordinal, data):
        line = self._line_for(ordinal)
        line.data = dict(data)
        line.text = self._render_entry(data)
        line.error = ""
        line.dirty = True

    def remove(self, ordinal):
        line = self._line_for(ordinal)
        self.lines.remove(line)

    def text(self):
        body = []
        for line in self.lines:
            # Untouched lines keep their original spelling, so opening and saving the editor
            # cannot reformat a file the user wrote by hand.
            if line.kind == "entry" and line.dirty:
                body.append(self._render_entry(line.data))
            else:
                body.append(line.text)
        return "\n".join(body).rstrip("\n") + "\n"

    def save(self):
        hbnscan.write_atomic(self.path, self.text())
        return self.path


# --------------------------------------------------------------------------- sources


def _parse_source_line(text, lineno):
    url = text.strip()
    if not url:
        return {}, f"line {lineno}: empty source"
    problem = source_problem(url)
    return {"url": url}, (f"line {lineno}: {problem}" if problem else "")


def _render_source(data):
    return data["url"].strip()


def source_problem(url):
    """A human-readable reason the URL cannot be used, or "" when it is fine."""
    candidate = (url or "").strip()
    if not candidate:
        return "the URL is empty"
    if any(char.isspace() for char in candidate):
        return "the URL contains a space"
    try:
        parts = urlparse(candidate)
    except ValueError:
        return "the URL cannot be parsed"
    if parts.scheme not in ("http", "https"):
        return "the URL must start with http:// or https://"
    if not parts.hostname:
        return "the URL has no host"
    return ""


def sources_document():
    return Document(hbnscan.FEEDS_PATH, _parse_source_line, _render_source)


def validate_source(url, others=()):
    problem = source_problem(url)
    if problem:
        raise ValueError(problem)
    if url.strip() in {value.strip() for value in others}:
        raise ValueError("that source is already in the list")


def add_source(url, position="end"):
    """Add a feed URL. `position="top"` puts it before the other entries."""
    document = sources_document()
    validate_source(url, document.entry_values())
    document.append({"url": url.strip()})
    if position == "top":
        entry_lines = [index for index, line in enumerate(document.lines)
                       if line.kind == "entry"]
        first, last = entry_lines[0], entry_lines[-1]
        if first != last:
            document.lines.insert(first, document.lines.pop(last))
    return document.save()


def remove_source(index):
    document = sources_document()
    document.remove(index)
    return document.save()


def update_source(index, url):
    document = sources_document()
    others = [value for position, value in enumerate(document.entry_values())
              if position != index]
    validate_source(url, others)
    document.update(index, {"url": url.strip()})
    return document.save()


def test_source(url, timeout=None):
    """Fetch one feed without storing anything, for the editor's Test button."""
    timeout = timeout or hbnscan.FETCH_TIMEOUT
    try:
        items, _etag, _modified = hbnscan.fetch_feed(url, timeout=timeout)
    except Exception as exc:                                    # noqa: BLE001 - reported
        return {"ok": False, "items": 0, "error": str(exc),
                "hint": hbnscan._feed_error_hint(exc)}
    return {"ok": True, "items": len(items), "error": "", "hint": ""}


# --------------------------------------------------------------------------- topics


def _parse_topic_line(text, lineno):
    parts = [part.strip() for part in text.split("|")]
    label = parts[0]
    severity = (parts[1].lower() if len(parts) > 1 and parts[1] else "medium")
    group = parts[2] if len(parts) > 2 and parts[2] else ""
    problem = topic_problem(label, severity, group)
    data = {"label": label, "severity": severity, "group": group}
    return data, (f"line {lineno}: {problem}" if problem else "")


def _render_topic(data):
    label = data["label"].strip()
    severity = data["severity"]
    group = (data.get("group") or "").strip()
    return f"{label} | {severity} | {group}" if group else f"{label} | {severity}"


def topic_problem(label, severity, group=""):
    label = (label or "").strip()
    if not label:
        return "the topic needs text"
    if len(label) > hbnscan.TOPIC_MAX_LENGTH:
        return f"the topic is longer than {hbnscan.TOPIC_MAX_LENGTH} characters"
    if "|" in label:
        return "the topic cannot contain '|'"
    if label.startswith("#"):
        return "the topic cannot start with '#'"
    if severity not in SEVERITY_LEVELS:
        return "the interest level must be high, medium or low"
    if len(group or "") > hbnscan.TOPIC_GROUP_MAX:
        return f"the group is longer than {hbnscan.TOPIC_GROUP_MAX} characters"
    return ""


def topics_document():
    """Only the topics.txt file. Profile-supplied topics are edited with the profile."""
    return Document(hbnscan.TOPICS_PATH, _parse_topic_line, _render_topic)


def file_topics():
    """Structured topics from topics.txt (not the active profile's own list)."""
    document = topics_document()
    return [data for _index, data, error in document.entries() if not error]


def validate_topic(label, severity, group="", others=()):
    problem = topic_problem(label, severity, group)
    if problem:
        raise ValueError(problem)
    if label.strip().lower() in {(value or "").strip().lower() for value in others}:
        raise ValueError("that topic is already in the list")


def add_topic(label, severity="medium", group=""):
    document = topics_document()
    validate_topic(label, severity, group, [data["label"] for _i, data, _e
                                            in document.entries()])
    document.append({"label": label.strip(), "severity": severity,
                     "group": (group or "").strip()})
    return document.save()


def update_topic(index, label, severity, group=""):
    document = topics_document()
    others = [data["label"] for position, data, _error in document.entries()
              if position != index]
    validate_topic(label, severity, group, others)
    document.update(index, {"label": label.strip(), "severity": severity,
                            "group": (group or "").strip()})
    return document.save()


def remove_topic(index):
    document = topics_document()
    document.remove(index)
    return document.save()


# --------------------------------------------------------------------------- targets


def _parse_target_line(text, lineno):
    parts = [part.strip() for part in text.split("|")]
    terms = [term.strip() for term in parts[0].split("&") if term.strip()]
    category = (parts[1].lower() if len(parts) > 1 else "")
    severity = (parts[2].lower() if len(parts) > 2 else "")
    note = parts[3] if len(parts) > 3 else ""
    problem = target_problem(terms, category, severity)
    return {"terms": terms, "category": category, "severity": severity, "note": note}, (
        f"line {lineno}: {problem}" if problem else "")


def _render_target(data):
    phrases = " & ".join(str(term).strip() for term in data["terms"])
    line = f"{phrases} | {data['category']} | {data['severity']}"
    if (data.get("note") or "").strip():
        line += f" | {data['note'].strip()}"
    return line


def target_problem(terms, category, severity, categories=None):
    phrases = [str(term).strip() for term in terms or [] if str(term).strip()]
    if not phrases:
        return "a target needs at least one phrase"
    if any("|" in phrase for phrase in phrases):
        return "a phrase cannot contain '|'"
    known = tuple(categories or hbnscan.CATEGORIES)
    if category not in known:
        return f"category '{category}' is not in the active profile ({', '.join(known)})"
    if severity not in SEVERITY_LEVELS:
        return "the status must be high, medium or low"
    return ""


def targets_document():
    return Document(hbnscan.WATCHLIST_PATH, _parse_target_line, _render_target)


def file_targets():
    document = targets_document()
    return [data for _index, data, error in document.entries() if not error]


def validate_target(terms, category, severity, note="", others=()):
    problem = target_problem(terms, category, severity)
    if problem:
        raise ValueError(problem)
    label = " & ".join(str(term).strip().lower() for term in terms).strip()
    if label in {(value or "").strip().lower() for value in others}:
        raise ValueError("that target is already in the list")


def add_target(terms, category, severity, note=""):
    document = targets_document()
    validate_target(terms, category, severity, note,
                    [" & ".join(data["terms"]) for _i, data, _e in document.entries()])
    document.append({"terms": [str(term).strip() for term in terms],
                     "category": category, "severity": severity, "note": note.strip()})
    return document.save()


def update_target(index, terms, category, severity, note=""):
    document = targets_document()
    others = [" & ".join(data["terms"]) for position, data, _error
              in document.entries() if position != index]
    validate_target(terms, category, severity, note, others)
    document.update(index, {"terms": [str(term).strip() for term in terms],
                            "category": category, "severity": severity,
                            "note": note.strip()})
    return document.save()


def remove_target(index):
    document = targets_document()
    document.remove(index)
    return document.save()


def watchlist_categories():
    """Categories a new target may use: the active profile's vocabulary."""
    return tuple(hbnscan.CATEGORIES)


# --------------------------------------------------------------------------- criteria


def profile_slug(name):
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return slug[:60] or "profile"


def builtin_profile_name():
    return str(hbnscan.DEFAULT_PROFILE["name"])


def profile_editor_data(choice=""):
    """The merged profile for editing, with the file it came from.

    `choice` is a name in profiles/, a path, or "" for the built-in default. The returned
    dict is complete (defaults filled in), so the form never shows a blank required field
    that is actually inherited.
    """
    path = hbnscan.resolve_profile_path(choice)
    data = dict(hbnscan.DEFAULT_PROFILE)
    if path is not None and path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stored = {}
        if isinstance(stored, dict):
            data.update({key: value for key, value in stored.items()
                         if key in hbnscan.DEFAULT_PROFILE})
    return {"data": data, "path": path, "builtin": path is None}


def validate_profile(data):
    """Return (errors, warnings) for a profile mapping. Never raises."""
    errors, warnings = [], []
    categories = [str(value).strip().lower() for value in data.get("categories") or []]
    if not str(data.get("name") or "").strip():
        errors.append("the profile needs a name")
    if not categories:
        errors.append("the profile needs at least one category")
    elif "other" not in categories:
        warnings.append("'other' is added automatically as a fallback category")
    duplicates = {value for value in categories if categories.count(value) > 1}
    if duplicates:
        errors.append("duplicate categories: " + ", ".join(sorted(duplicates)))
    core = [str(value).strip().lower() for value in data.get("core_categories") or []]
    unknown_core = [value for value in core if value not in categories]
    if unknown_core:
        errors.append("core categories not in the category list: " + ", ".join(unknown_core))
    if not core:
        warnings.append("no core categories: every signal will rank as context")

    for name, values in (data.get("category_hints") or []):
        if str(name).strip().lower() not in categories:
            errors.append(f"category hint '{name}' is not a category")
        if not values:
            warnings.append(f"category hint '{name}' has no phrases")
    if not data.get("strong_signals"):
        warnings.append("no strong signals: the offline heuristic will flag very little")
    if not data.get("enforcement_signals"):
        warnings.append("no enforcement signals: core items cannot be graded high offline")
    for field_name in ("severity_high", "severity_medium", "severity_low"):
        if not str(data.get(field_name) or "").strip():
            warnings.append(f"{field_name} is empty: the model gets no grading rule")

    topic_labels = []
    for entry in data.get("topics") or []:
        if isinstance(entry, dict):
            label, severity = entry.get("label", ""), entry.get("severity", "medium")
            group = entry.get("group", "")
        else:
            parts = [part.strip() for part in str(entry).split("|")]
            label = parts[0] if parts else ""
            severity = parts[1] if len(parts) > 1 and parts[1] else "medium"
            group = parts[2] if len(parts) > 2 else ""
        problem = topic_problem(label, severity, group)
        if problem:
            errors.append(f"topic '{label}': {problem}")
        elif str(label).strip().lower() in topic_labels:
            errors.append(f"duplicate topic '{label}'")
        else:
            topic_labels.append(str(label).strip().lower())

    for entry in data.get("targets") or []:
        if isinstance(entry, dict):
            raw = entry.get("match") or entry.get("terms") or entry.get("phrase") or []
            terms = raw.split("&") if isinstance(raw, str) else list(raw)
            category = str(entry.get("category", "")).lower()
            severity = str(entry.get("severity", "")).lower()
        else:
            parts = [part.strip() for part in str(entry).split("|")]
            terms = parts[0].split("&") if parts else []
            category = parts[1].lower() if len(parts) > 1 else ""
            severity = parts[2].lower() if len(parts) > 2 else ""
        problem = target_problem(terms, category, severity, categories)
        if problem:
            errors.append(f"target '{' & '.join(terms)}': {problem}")

    return errors, warnings


def save_profile(choice, data, overwrite=False):
    """Write a profile file and return its path.

    `choice` is the name to save under. Refuses to overwrite an existing file unless
    `overwrite` is true, so "Save as" can never silently clobber someone's profile.
    """
    errors, _warnings = validate_profile(data)
    if errors:
        raise ValueError("; ".join(errors))
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("the profile needs a name")
    slug = profile_slug(choice if choice and choice.strip() else name)
    directory = hbnscan.APP_DIR / "profiles"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{slug}.json"
    if path.exists() and not overwrite:
        raise ValueError(f"profiles/{path.name} already exists; choose another name "
                         "or confirm overwriting")
    payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    hbnscan.write_atomic(path, payload)
    return path


def activate_profile(name):
    """Persist a profile choice and bind it in this process. Returns the active Profile."""
    hbnscan.save_settings(profile=name or "")
    return hbnscan.apply_profile()


# --------------------------------------------------------------------------- .env


def env_limits():
    """Current scan-budget values, as stored (not re-derived from defaults)."""
    env = hbnscan.read_env_values()

    def as_int(key, default):
        try:
            return int(env.get(key) or default)
        except (TypeError, ValueError):
            return default

    def as_flag(key, default):
        raw = str(env.get(key, "")).strip().lower()
        if not raw:
            return default
        return raw not in ("0", "false", "no", "off")

    return {
        "deep_read": as_flag("WATCHDOG_DEEP_READ", True),
        "briefing": as_flag("WATCHDOG_BRIEFING", True),
        "max_triage": as_int("WATCHDOG_MAX_TRIAGE", 40),
        "max_deep_reads": as_int("WATCHDOG_MAX_DEEP_READS", hbnscan.MAX_DEEP_READS),
        "brief_hours": as_int("WATCHDOG_BRIEF_HOURS", 24),
    }


def save_env_limits(deep_read=None, briefing=None, max_triage=None,
                    max_deep_reads=None, brief_hours=None):
    """Persist the budget switches to .env and apply them without a restart."""
    values = {}
    checks = (("max_triage", max_triage, 0, 500), ("max_deep_reads", max_deep_reads, 0, 200),
              ("brief_hours", brief_hours, 1, 720))
    for label, value, low, high in checks:
        if value is None:
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{label.replace('_', ' ')} must be a whole number") from None
        if not low <= number <= high:
            raise ValueError(f"{label.replace('_', ' ')} must be between {low} and {high}")
        values["WATCHDOG_" + label.upper()] = str(number)
    for key, flag in (("WATCHDOG_DEEP_READ", deep_read), ("WATCHDOG_BRIEFING", briefing)):
        if flag is not None:
            values[key] = "1" if flag else "0"
    if values:
        hbnscan.update_env_file(**values)
    return hbnscan.reload_env_limits()


def notification_editor():
    """Current notification transport values for an editable form."""
    env = hbnscan.read_env_values()
    return {
        "smtp_host": env.get("SMTP_HOST", ""),
        "smtp_port": env.get("SMTP_PORT", ""),
        "smtp_security": env.get("SMTP_SECURITY", "starttls") or "starttls",
        "smtp_user": env.get("SMTP_USER", ""),
        "smtp_password": env.get("SMTP_PASSWORD", ""),
        "smtp_from": env.get("SMTP_FROM", ""),
        "email_to": env.get("NOTIFY_EMAIL_TO", ""),
        "sms_to": env.get("NOTIFY_SMS_TO", ""),
        "sms_gateway": env.get("SMS_GATEWAY_DOMAIN", ""),
    }


def save_notifications(smtp_host="", smtp_port="", smtp_security="starttls",
                       smtp_user="", smtp_password=None, smtp_from="",
                       email_to="", sms_to="", sms_gateway=""):
    """Persist SMTP/SMS transport. `smtp_password=None` keeps the stored password."""
    if smtp_port not in ("", None):
        if not str(smtp_port).isdigit() or not 1 <= int(smtp_port) <= 65535:
            raise ValueError("the SMTP port must be a number between 1 and 65535")
    if smtp_security not in SMTP_SECURITY:
        raise ValueError("security must be starttls, ssl or none")
    values = {
        "SMTP_HOST": (smtp_host or "").strip(),
        "SMTP_PORT": str(smtp_port or "").strip(),
        "SMTP_SECURITY": smtp_security,
        "SMTP_USER": (smtp_user or "").strip(),
        "SMTP_FROM": (smtp_from or "").strip(),
        "NOTIFY_EMAIL_TO": (email_to or "").strip(),
        "NOTIFY_SMS_TO": (sms_to or "").strip(),
        "SMS_GATEWAY_DOMAIN": (sms_gateway or "").strip(),
    }
    if smtp_password is not None and smtp_password != "":
        values["SMTP_PASSWORD"] = smtp_password
    hbnscan.update_env_file(**values)
    return notification_editor()


def ai_editor():
    return {
        "model": hbnscan.LLM_MODEL,
        "base_url": hbnscan.LLM_BASE_URL,
        "configured": hbnscan.deepseek_configured(),
        "key_hint": (f"{hbnscan.LLM_API_KEY[:6]}…{hbnscan.LLM_API_KEY[-4:]}"
                     if hbnscan.LLM_API_KEY else ""),
    }


# --------------------------------------------------------------------------- diagnostics


def diagnose(conn=None, feeds=None):
    """Check every configuration input and return findings, most serious first.

    Each finding is `{"level": "error"|"warning"|"info", "area": str, "message": str}`.
    Nothing here changes state: it is the "what is misconfigured?" view.
    """
    findings = []

    def add(level, area, message):
        findings.append({"level": level, "area": area, "message": message})

    # Sources
    sources = sources_document()
    entries = sources.entries()
    valid_urls = []
    seen = set()
    for index, data, error in entries:
        if error:
            add("error", "Sources", error)
            continue
        url = data.get("url", "")
        problem = source_problem(url)
        if problem:
            add("error", "Sources", f"{url}: {problem}")
            continue
        if url in seen:
            add("warning", "Sources", f"{url} appears more than once")
        seen.add(url)
        valid_urls.append(url)
        if re.search(r"news\.google\.com/rss/articles/", url):
            add("warning", "Sources",
                f"{url} is a Google News article link, not a search feed; "
                "use /rss/search?q=…")
    if not entries:
        add("warning", "Sources", "no sources configured: scans will find nothing")

    # Topics
    topics = topics_document()
    topic_entries = topics.entries()
    active_topics = []
    for _index, data, error in topic_entries:
        if error:
            add("error", "Topics", error)
        else:
            active_topics.append(data)
    if len(active_topics) > hbnscan.TOPIC_LIMIT:
        add("error", "Topics",
            f"{len(active_topics)} topics configured; only the first {hbnscan.TOPIC_LIMIT} "
            "are used")
    settings = hbnscan.load_settings()
    if settings["topics_as_feeds"] and len(active_topics) > hbnscan.TOPIC_FEED_LIMIT:
        add("warning", "Topics",
            f"topics as feeds is on: only the first {hbnscan.TOPIC_FEED_LIMIT} topics become "
            f"query feeds ({len(active_topics)} active)")
    if not active_topics:
        add("info", "Topics", "no standing topics: only profile topics and keyword scoring apply")

    # Targets
    targets = targets_document()
    target_labels = []
    for _index, data, error in targets.entries():
        if error:
            add("error", "Targets", error)
            continue
        problem = target_problem(data["terms"], data["category"], data["severity"])
        if problem:
            add("error", "Targets", f"{' & '.join(data['terms'])}: {problem}")
            continue
        label = " & ".join(data["terms"]).lower()
        if label in target_labels:
            add("warning", "Targets", f"duplicate target rule: {label}")
        target_labels.append(label)
    if not target_labels:
        add("info", "Targets", "no pinned targets: relevance is inferred, never guaranteed")

    # Criteria (active profile)
    profile = hbnscan.current_profile()
    errors, warnings = validate_profile({
        "name": profile.name,
        "categories": list(profile.categories),
        "core_categories": list(profile.core_categories),
        "category_hints": [list(entry) for entry in profile.category_hints],
        "strong_signals": list(profile.strong_signals),
        "weak_signals": list(profile.weak_signals),
        "noise_title_signals": list(profile.noise_title_signals),
        "enforcement_signals": list(profile.enforcement_signals),
        "severity_high": profile.severity_high,
        "severity_medium": profile.severity_medium,
        "severity_low": profile.severity_low,
        "topics": [dict(entry) for entry in profile.topics],
        "targets": [dict(entry) for entry in profile.targets],
    })
    for message in errors:
        add("error", "Criteria", message)
    for message in warnings:
        add("warning", "Criteria", message)

    # AI and budgets
    if not hbnscan.deepseek_configured():
        add("info", "AI", "no API key: the offline heuristic runs, no deep reads or briefing")
    limits = env_limits()
    if limits["max_triage"] == 0 and hbnscan.deepseek_configured():
        add("warning", "AI", "AI triage budget is 0: every item is scored offline")
    if not limits["deep_read"]:
        add("info", "AI", "deep reads are disabled: verdicts stay headline-level")
    if not limits["briefing"]:
        add("info", "AI", "briefing generation is disabled")

    # Notifications
    if settings["notify_enabled"]:
        config = hbnscan.notification_config()
        if not hbnscan.notifications_configured(config):
            add("error", "Notifications",
                "notifications are on but SMTP is not configured; nothing can be sent")
        for address in config["unresolved_sms"]:
            add("warning", "Notifications",
                f"SMS number {address} has no gateway domain; set SMS_GATEWAY_DOMAIN")

    # Scan shape
    if settings["max_new_per_feed"] > 200:
        add("warning", "Scanning",
            "max new items per feed is high: a single scan can take a long time")
    if settings["retention_days"] < 30:
        add("warning", "Scanning",
            "retention under 30 days removes dismissed history quickly")

    # Sources that are failing, from the last scan
    if conn is not None:
        try:
            health = hbnscan_source_health(conn, feeds)
        except Exception:                                        # noqa: BLE001 - optional
            health = []
        for entry in health:
            if entry["status"] == "failing":
                add("warning", "Sources",
                    f"{entry['source']} failed its last fetch: {entry['error']}")
            elif entry["status"] == "never":
                add("info", "Sources", f"{entry['source']} has not been fetched yet")

    order = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda finding: (order[finding["level"]], finding["area"]))
    return findings


def hbnscan_source_health(conn, feeds=None):
    """Late import keeps `hbconfig` free of a hard dependency on the workspace module."""
    import hbworkspace
    return hbworkspace.source_health(conn, feeds)


def summarise(findings):
    counts = {"error": 0, "warning": 0, "info": 0}
    for finding in findings:
        counts[finding["level"]] += 1
    if counts["error"]:
        return f"{counts['error']} error(s), {counts['warning']} warning(s)"
    if counts["warning"]:
        return f"{counts['warning']} warning(s)"
    return "configuration looks consistent"
