#!/usr/bin/env python3
"""
HB Briefing - canonical intelligence records

The engine has always stored one row per feed item, with the verdict columns bolted onto it.
That is a workable *storage* shape, but it conflates things that behave differently: a
publisher is not an article, an article is not a story, an assessment is not a signal, and a
re-classification is a new judgement of the same thing rather than a replacement of the
evidence.

This module is the vocabulary. It defines the records the rest of the application reasons in,
gives each a stable identifier, and says which record owns which fact. Storage adapters live
here too, so a `seen_items` row can be projected into a `Signal` plus an `Assessment` without
any front end guessing at column meanings.

Design rules:

  * **Nothing is inferred twice.** An identifier is derived from the identity of the thing it
    names — a canonical URL, a story key, a signal identity — never from a row number or a
    timestamp. Re-running a classification therefore yields the same ids.
  * **Evidence outlives opinion.** `Signal` carries the article it came from; `Assessment`
    carries the judgement about it. Reclassifying adds an `Assessment`; it never edits or
    deletes the signal or the article behind it.
  * **Versions are explicit.** Every record declares the contract it was written against, so a
    stored JSON document can be migrated rather than misread.
  * **Uncertainty is data.** `Claim.status`, `Article.completeness` and `Alert.state` exist so
    the application can say "unknown" instead of guessing.

The module is pure standard library and does not import the engine, which keeps the dependency
one-way: front ends and `hbnscan` both read `hbrecords`.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit

# Bumped when a field is renamed, retyped or removed. Readers refuse a newer contract rather
# than silently dropping fields they do not understand.
CONTRACT_VERSION = 1

# Tracking parameters that carry no identity. Kept here because canonicalisation is what makes
# an article id stable across the same link shared by two feeds.
TRACKING_PARAMS = frozenset((
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "referrer", "source", "cmpid",
))

# Record kinds, in the order the directive lists them. The prefix is part of every id, so a
# bare identifier is self-describing in a log line or an interchange file.
KIND_PREFIXES = {
    "source": "src",
    "source_occurrence": "occ",
    "article": "art",
    "story": "sto",
    "claim": "clm",
    "signal": "sig",
    "assessment": "asm",
    "alert": "alt",
    "dossier": "dos",
    "action": "act",
}


def _digest(value, size=16):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:size]


def make_id(kind, *parts):
    """`<prefix>:<digest>` for one kind of record.

    The parts are joined with a unit separator so ("a", "bc") and ("ab", "c") cannot collide.
    """
    try:
        prefix = KIND_PREFIXES[kind]
    except KeyError:
        raise ValueError(f"unknown record kind {kind!r}") from None
    return f"{prefix}:{_digest(chr(31).join(str(part) for part in parts))}"


def canonical_url(url):
    """A comparable form of an article URL.

    Host case, a leading `www.`, the fragment and tracking parameters are dropped and the
    remaining query is sorted, so the same article linked from two places compares equal.
    The scheme is normalised to https: the same article served over both schemes is one
    article, and identity must not depend on which link a feed happened to publish.
    Returns "" for anything that is not http(s) with a host.
    """
    text = (url or "").strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https"):
        return ""
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return ""
    pairs = sorted((key, value) for key, value in parse_qsl(parts.query)
                   if key.lower() not in TRACKING_PARAMS)
    suffix = f":{port}" if port and port not in (80, 443) else ""
    path = parts.path.rstrip("/")
    return f"https://{host}{suffix}{path}" + (f"?{urlencode(pairs)}" if pairs else "")


def publisher_of(url):
    """The host that published a URL, without `www.`; "" when there is none."""
    try:
        host = (urlsplit((url or "").strip()).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _now():
    return datetime.now().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- records


@dataclass
class Record:
    """Fields every record shares: identity, kind, contract and how it came to exist."""

    id: str
    kind: str = ""
    contract: int = CONTRACT_VERSION
    recorded_at: str = field(default_factory=_now)
    # Who produced this record and by what method. A model-assisted assessment records the
    # provider, model and evidence set here; the deterministic path records its rule layer.
    provenance: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.kind:
            self.kind = type(self).kind_name
        if self.contract > CONTRACT_VERSION:
            raise ValueError(
                f"{self.kind} record declares contract {self.contract}; "
                f"this build understands {CONTRACT_VERSION}")

    kind_name = ""

    def to_dict(self):
        data = asdict(self)
        # Tuples become lists in JSON; keep the in-memory type predictable by round-tripping.
        return data

    def to_json(self):
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


@dataclass
class Source(Record):
    """A publisher and the endpoint through which its material arrives.

    One `Source` per configured feed/endpoint. The publisher is a separate field because a
    feed host and the publisher are not always the same organisation, and the application
    must not present one as the other.
    """

    kind_name = "source"

    name: str = ""
    feed_url: str = ""
    publisher: str = ""
    publisher_url: str = ""
    source_type: str = "feed"          # feed | official | api | query | document | mail | user
    categories: tuple = ()
    language: str = ""
    country: str = ""
    # A user preference, never an objective factual rating.
    trust: str = "unrated"
    enabled: bool = True
    schedule: dict = field(default_factory=dict)


@dataclass
class SourceOccurrence(Record):
    """One observation of one item through one feed.

    The same article seen through three feeds is three occurrences of one article. Counting
    occurrences as corroboration is exactly the mistake this record exists to prevent.
    """

    kind_name = "source_occurrence"

    source_id: str = ""
    article_id: str = ""
    feed_url: str = ""
    guid: str = ""                     # the link or id as the feed delivered it
    published: str = ""
    published_is_derived: bool = False
    first_seen_at: str = ""


@dataclass
class Article(Record):
    """A published document, with what is known about how completely it was retrieved."""

    kind_name = "article"

    canonical_url: str = ""
    original_url: str = ""
    publisher: str = ""
    title: str = ""
    author: str = ""
    published: str = ""
    retrieved_at: str = ""
    content_hash: str = ""
    summary: str = ""                  # the publisher's own summary, as delivered
    text: str = ""
    content_type: str = ""
    # none | feed_summary | html | pdf | hook | user
    extraction: str = "none"
    # headline | summary | partial | full | official | user | unavailable
    completeness: str = "headline"
    # ok | http_error | blocked | too_large | not_extracted
    retrieval_outcome: str = "ok"
    extraction_version: int = CONTRACT_VERSION


@dataclass
class Story(Record):
    """A development that may be represented by several articles.

    `keys` are the equality keys that justify the grouping. Membership is recorded, never
    inferred at read time, so a merge or split can be replayed.
    """

    kind_name = "story"

    title: str = ""
    keys: tuple = ()
    article_ids: tuple = ()
    source_ids: tuple = ()


@dataclass
class Claim(Record):
    """A factual assertion attributed to identifiable evidence.

    `status` distinguishes what a publisher said from what can be corroborated. A claim is
    never upgraded to "corroborated" because a model repeated it.
    """

    kind_name = "claim"

    statement: str = ""
    asserted_in: str = ""              # article_id the assertion came from
    attributed_to: str = ""            # source_id of the publisher making it
    evidence: tuple = ()               # article_ids supporting the statement
    corroborating_sources: tuple = ()  # source_ids, only when independence is known
    contradicting_evidence: tuple = ()
    status: str = "asserted"           # asserted | corroborated | contradicted | unresolved
    confidence: float = 0.0
    uncertainty: str = ""


@dataclass
class Signal(Record):
    """An assessed development that may matter to the user.

    Identity is the article, not the verdict, so re-assessing does not create a second signal
    and does not orphan its history.
    """

    kind_name = "signal"

    article_id: str = ""
    occurrence_ids: tuple = ()
    story_id: str = ""
    title: str = ""
    # The current view, denormalised for reading. The authoritative history is a chain of
    # Assessment records; these fields exist so a grid does not need a join per row.
    category: str = ""
    severity: str = ""
    flagged: bool = False
    dismissed: bool = False
    entities: tuple = ()
    facts: tuple = ()
    action: str = ""
    confidence: float = 0.0
    layer: str = ""                    # watchlist | topic | core | context | heuristic | model
    reason: str = ""
    first_seen_at: str = ""
    latest_assessment_id: str = ""


@dataclass
class Assessment(Record):
    """One interpretation of a signal under one profile, at one time.

    Append-only. `supersedes` links a re-assessment to the one it replaced, which is what
    makes "what changed and why" answerable without rewriting history.
    """

    kind_name = "assessment"

    signal_id: str = ""
    article_id: str = ""
    profile: str = ""
    method: str = "heuristic"          # watchlist | topic | heuristic | model | deep_read | user
    layer: str = ""
    category: str = ""
    severity: str = ""
    flagged: bool = False
    confidence: float = 0.0
    analysis: str = ""
    reason: str = ""
    evidence: tuple = ()
    supersedes: str = ""
    # The axes behind this judgement and the reason for each, as produced by
    # `hbnscan.explain_assessment`. Kept on the record so history explains itself without
    # re-deriving today's answer from data the assessment never saw.
    dimensions: dict = field(default_factory=dict)
    rationale: tuple = ()


@dataclass
class Alert(Record):
    """A notification generated under an explicit rule.

    `state` refuses to claim delivery that was not confirmed: `sent` requires a transport
    acknowledgement, and an ambiguous retry stays `uncertain`.
    """

    kind_name = "alert"

    signal_id: str = ""
    channel: str = "webhook"           # webhook | email | sms | desktop
    rule: str = ""
    state: str = "queued"              # queued | sent | failed | uncertain | suppressed
    attempted_at: str = ""
    detail: str = ""


@dataclass
class Dossier(Record):
    """A persistent research collection.

    `row_id` is the database-local identity; `id` is the portable one. Renaming a dossier
    changes its `name`, never its identity.
    """

    kind_name = "dossier"

    row_id: int = 0
    name: str = ""
    summary: str = ""
    dossier_type: str = "subject"
    status: str = "open"
    created_at: str = ""
    updated_at: str = ""
    signal_ids: tuple = ()


@dataclass
class Action(Record):
    """A follow-up task. A suggestion is not an obligation until the user confirms it."""

    kind_name = "action"

    row_id: int = 0
    title: str = ""
    detail: str = ""
    dossier_id: str = ""
    item_hash: str = ""                # the stored signal row this action came from
    signal_id: str = ""
    status: str = "open"
    priority: str = "medium"
    due_date: str = ""
    suggested_by: str = ""             # "" for user-created; "model" | "rule:<name>"
    confirmed: bool = False
    created_at: str = ""
    updated_at: str = ""
    completed_at: str = ""


RECORD_TYPES = {
    "source": Source,
    "source_occurrence": SourceOccurrence,
    "article": Article,
    "story": Story,
    "claim": Claim,
    "signal": Signal,
    "assessment": Assessment,
    "alert": Alert,
    "dossier": Dossier,
    "action": Action,
}


def from_dict(data):
    """Rebuild a record from a mapping, refusing a kind or contract this build cannot read."""
    if not isinstance(data, dict):
        raise ValueError("a record must be a JSON object")
    kind = data.get("kind") or ""
    try:
        cls = RECORD_TYPES[kind]
    except KeyError:
        raise ValueError(f"unknown record kind {kind!r}") from None
    contract = data.get("contract", CONTRACT_VERSION)
    if not isinstance(contract, int) or contract > CONTRACT_VERSION:
        raise ValueError(f"{kind} record uses contract {contract!r}; "
                         f"this build understands {CONTRACT_VERSION}")
    known = {f for f in cls.__dataclass_fields__}
    payload = {key: value for key, value in data.items() if key in known}
    for name, value in list(payload.items()):
        default = getattr(cls, name, None)
        if isinstance(default, tuple) and isinstance(value, list):
            payload[name] = tuple(value)
    # A record written by an older contract keeps its own number, so a migration can find it.
    payload["contract"] = contract
    return cls(**payload)


def from_json(text):
    return from_dict(json.loads(text))


def envelope(record):
    """The portable form used by interchange files: kind, contract and id up front."""
    return {"kind": record.kind, "contract": record.contract, "id": record.id,
            "record": record.to_dict()}


# --------------------------------------------------------------------------- identities
#
# One function per identity, so the application cannot invent a second convention.


def source_id(feed_url):
    """A source is identified by the endpoint it is configured with."""
    return make_id("source", (feed_url or "").strip().lower())


def article_id(url):
    """An article is identified by its canonical URL, falling back to the raw link."""
    canonical = canonical_url(url) or (url or "").strip()
    return make_id("article", canonical.lower())


def occurrence_id(feed_url, guid):
    """An occurrence is the (source, delivered identifier) pair, never the article alone."""
    return make_id("source_occurrence", (feed_url or "").strip().lower(),
                   (guid or "").strip())


def story_id(*keys):
    """A story is identified by the equality keys that justify grouping it."""
    usable = sorted(key for key in keys if key)
    if not usable:
        raise ValueError("a story needs at least one key")
    return make_id("story", *usable)


def claim_id(statement, asserted_in=""):
    """A claim is identified by what it asserts and where that assertion was found."""
    return make_id("claim", " ".join((statement or "").split()).lower(), asserted_in)


def signal_id(article_id_value, fallback=""):
    """A signal is identified by its evidence, so re-assessment keeps the same signal."""
    return make_id("signal", article_id_value or fallback)


def assessment_id(signal, profile, method, category, severity, analysis, supersedes=""):
    """An assessment is identified by the judgement and the one it superseded."""
    return make_id("assessment", signal, profile, method, category, severity,
                   _digest(analysis, 32), supersedes)


def alert_id(signal, channel, rule):
    return make_id("alert", signal, channel, rule)


def dossier_id(row_id):
    return make_id("dossier", int(row_id))


def action_id(row_id):
    return make_id("action", int(row_id))


# --------------------------------------------------------------------------- storage adapters
#
# `seen_items` is one row per observed item, with the verdict columns alongside. These
# functions project it into the canonical records without any caller re-deriving meanings.


def _get(row, key, default=""):
    """Read a column when it exists.

    Older databases predate several columns, and a row from `select_signals` and one from
    `SELECT *` do not carry the same set. Reading defensively here keeps every caller from
    repeating the same `in row.keys()` dance.
    """
    if row is None:
        return default
    try:
        if key not in row.keys():
            return default
    except AttributeError:
        return row.get(key, default)
    value = row[key]
    return default if value is None else value


def _json_list(value):
    if isinstance(value, (list, tuple)):
        return tuple(value)
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return ()
    return tuple(parsed) if isinstance(parsed, list) else ()


def _signal_identity(row):
    """The identity a signal keeps across re-assessment: its article, else its item hash."""
    link = _get(row, "link")
    canonical = canonical_url(link)
    return article_id(link) if canonical else f"hash:{_get(row, 'item_hash')}"


def article_from_row(row):
    """Project a stored item into an `Article`.

    `completeness` is honest about what the row holds: the engine keeps the publisher's
    summary and an excerpt, not the article body, unless the article was actually read.
    """
    link = _get(row, "link")
    summary = _get(row, "excerpt")
    deep_read = bool(_get(row, "deep_read", 0))
    return Article(
        id=article_id(link),
        canonical_url=canonical_url(link) or link,
        original_url=link,
        publisher=publisher_of(link),
        title=_get(row, "title"),
        published=_get(row, "published"),
        retrieved_at=_get(row, "seen_at"),
        summary=summary,
        completeness="partial" if deep_read else ("summary" if summary else "headline"),
        extraction="html" if deep_read else "feed_summary",
        retrieval_outcome="ok" if deep_read else "not_extracted",
    )


def occurrence_from_row(row):
    """Project a stored item into the occurrence that produced it."""
    feed_url = _get(row, "feed_url")
    link = _get(row, "link")
    return SourceOccurrence(
        id=occurrence_id(feed_url, link),
        source_id=source_id(feed_url),
        article_id=article_id(link),
        feed_url=feed_url,
        guid=link,
        published=_get(row, "published"),
        first_seen_at=_get(row, "seen_at"),
    )


def signal_from_row(row):
    """Project a stored item into a `Signal`, including the current assessment's view."""
    link = _get(row, "link")
    cluster = _get(row, "cluster_key")
    return Signal(
        id=signal_id(_signal_identity(row)),
        article_id=article_id(link),
        occurrence_ids=(occurrence_id(_get(row, "feed_url"), link),),
        story_id=story_id(cluster) if cluster else "",
        title=_get(row, "title"),
        category=_get(row, "category"),
        severity=_get(row, "severity"),
        flagged=bool(_get(row, "flagged", 0)),
        dismissed=bool(_get(row, "dismissed", 0)),
        entities=_json_list(_get(row, "entities", "[]")),
        facts=_json_list(_get(row, "facts", "[]")),
        action=_get(row, "action"),
        confidence=float(_get(row, "confidence", 0.0) or 0.0),
        layer=_get(row, "source_layer"),
        reason=_get(row, "verdict_reason"),
        first_seen_at=_get(row, "seen_at"),
    )


def assessment_for(item_hash_value, link, *, profile="", method="heuristic", layer="",
                   category="", severity="", flagged=False, confidence=0.0, analysis="",
                   reason="", supersedes="", evidence=(), dimensions=None, rationale=()):
    """Build an `Assessment` from explicit values, as the engine has them at write time.

    The assessment id is scoped to the stored row, not only to the article: two rows can share
    a link while carrying different headlines (and therefore different item hashes), and one
    must never overwrite the other's history. `signal_id` stays article-based, because the
    signal is the same development either way.
    """
    canonical = canonical_url(link)
    article = article_id(link) if canonical else f"hash:{item_hash_value}"
    return Assessment(
        id=assessment_id(f"{item_hash_value}|{article}", profile, method, category, severity,
                         analysis, supersedes),
        signal_id=signal_id(article),
        article_id=article_id(link),
        profile=profile,
        method=method or "heuristic",
        layer=layer,
        category=category,
        severity=severity,
        flagged=bool(flagged),
        confidence=float(confidence or 0.0),
        analysis=analysis,
        reason=reason,
        evidence=tuple(evidence),
        supersedes=supersedes,
        dimensions=dict(dimensions or {}),
        rationale=tuple(rationale),
    )


def assessment_from_row(row, profile="", supersedes="", method=""):
    """Project a stored item's current verdict into an `Assessment`."""
    layer = _get(row, "source_layer")
    return assessment_for(
        _get(row, "item_hash"), _get(row, "link"),
        profile=profile,
        method=method or layer or "heuristic",
        layer=layer,
        category=_get(row, "category"),
        severity=_get(row, "severity"),
        flagged=bool(_get(row, "flagged", 0)),
        confidence=float(_get(row, "confidence", 0.0) or 0.0),
        analysis=_get(row, "summary"),
        reason=_get(row, "verdict_reason"),
        supersedes=supersedes,
    )


def dossier_from_row(row, signal_ids=()):
    return Dossier(
        id=dossier_id(_get(row, "id", 0)),
        row_id=int(_get(row, "id", 0) or 0),
        name=_get(row, "name"),
        summary=_get(row, "summary"),
        dossier_type=_get(row, "kind", "subject"),
        status=_get(row, "status", "open"),
        created_at=_get(row, "created_at"),
        updated_at=_get(row, "updated_at"),
        signal_ids=tuple(signal_ids),
    )


def action_from_row(row, signal_id_value=""):
    """Project an action row.

    `signal_id_value` is passed by a caller that has already resolved the stored item to a
    signal; without it the action keeps the raw `item_hash` reference rather than inventing
    an identifier it cannot verify.
    """
    dossier_row_id = _get(row, "dossier_id", 0)
    return Action(
        id=action_id(_get(row, "id", 0)),
        row_id=int(_get(row, "id", 0) or 0),
        title=_get(row, "title"),
        detail=_get(row, "detail"),
        dossier_id=dossier_id(dossier_row_id) if dossier_row_id else "",
        item_hash=_get(row, "item_hash"),
        signal_id=signal_id_value,
        status=_get(row, "status", "open"),
        priority=_get(row, "priority", "medium"),
        due_date=_get(row, "due_date"),
        created_at=_get(row, "created_at"),
        updated_at=_get(row, "updated_at"),
        completed_at=_get(row, "completed_at"),
    )
