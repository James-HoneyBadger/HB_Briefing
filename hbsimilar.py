#!/usr/bin/env python3
"""
HB Briefing - near-duplicate story detection

Cross-feed dedupe used to rely on identical canonical URLs or identical normalised headlines,
which misses the common case: the same wire story republished under a rewritten headline.

This module scores *content* overlap with shingles, Jaccard similarity and MinHash. Everything
is deterministic and standard-library only:

1. `shingles(text)` turns the first part of a title+summary into overlapping n-grams.
2. `signature(shingles)` is a MinHash sketch: 64 fixed permutations, so two sketches can be
   compared with a single pass and stored compactly.
3. `bands(signature)` splits the sketch into 16 band keys. Two documents sharing any band key
   are candidates (locality-sensitive hashing), which keeps the comparison count small instead
   of comparing every new item with every stored one.
4. `jaccard(a, b)` is the exact score; only candidates that pass the LSH filter pay for it.

The caller decides the threshold (`WATCHDOG_SIMILARITY`, default 0.7). Near-duplicates are
grouped into the same story; they are never silently discarded.
"""
import hashlib
import re

SHINGLE_WORDS = 4
SIGNATURE_SIZE = 64
BAND_COUNT = 16
ROWS_PER_BAND = SIGNATURE_SIZE // BAND_COUNT
PRIME = (1 << 61) - 1
TEXT_LIMIT = 500

_WORD_RE = re.compile(r"[a-z0-9]+")


def _hash_bytes(value):
    return int.from_bytes(hashlib.blake2b(value.encode("utf-8"), digest_size=8).digest(), "big")


def _coefficients(size=SIGNATURE_SIZE):
    """Fixed (a, b) pairs, derived from the index so the sketch never changes between runs."""
    pairs = []
    for index in range(size):
        a = _hash_bytes(f"a:{index}") % (PRIME - 1) + 1
        b = _hash_bytes(f"b:{index}") % PRIME
        pairs.append((a, b))
    return tuple(pairs)


COEFFICIENTS = _coefficients()


def normalise(text, limit=TEXT_LIMIT):
    """Lower-case words, punctuation dropped, capped so cost per item is bounded."""
    words = _WORD_RE.findall((text or "").lower())
    return " ".join(words)[:limit]


def shingles(text, size=SHINGLE_WORDS):
    """Overlapping word n-grams. Very short text becomes a single shingle (or none)."""
    words = _WORD_RE.findall((text or "").lower())
    if not words:
        return set()
    if len(words) <= size:
        return {" ".join(words)}
    return {" ".join(words[index:index + size])
            for index in range(len(words) - size + 1)}


def signature(shingle_set):
    """64-value MinHash sketch; empty input has an empty sketch."""
    if not shingle_set:
        return ()
    hashes = [_hash_bytes(shingle) for shingle in shingle_set]
    return tuple(min((a * value + b) % PRIME for value in hashes)
                 for a, b in COEFFICIENTS)


def bands(minhash):
    """(band index, band key) pairs for locality-sensitive candidate lookup."""
    out = []
    for band in range(BAND_COUNT):
        start = band * ROWS_PER_BAND
        chunk = minhash[start:start + ROWS_PER_BAND]
        if len(chunk) < ROWS_PER_BAND:
            break
        out.append((band, format(_hash_bytes(",".join(str(value) for value in chunk)), "x")))
    return out


def jaccard(left, right):
    """Exact Jaccard similarity of two shingle sets, in 0.0-1.0."""
    if not left or not right:
        return 0.0
    union = len(left | right)
    return len(left & right) / union if union else 0.0


def similarity(left_text, right_text):
    """Convenience: shingles + Jaccard for two strings."""
    return jaccard(shingles(left_text), shingles(right_text))


def document_text(title, excerpt=""):
    """The canonical text a sketch is built from: headline plus the bounded summary."""
    return normalise(f"{title or ''} {excerpt or ''}")
