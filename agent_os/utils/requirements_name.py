"""Utilities for deriving a project name and repo slug from a requirements YAML/MD file."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import yaml

_GENERIC_TITLES: frozenset[str] = frozenset({
    "imported requirements", "general", "imported features", "",
})

_STOP_WORDS: frozenset[str] = frozenset({
    "a", "an", "the", "and", "or", "of", "to", "in", "for",
    "is", "as", "so", "that", "can", "be", "with", "on", "by",
    "i", "my", "we", "our", "from", "its", "it", "at", "all",
    "view", "manage", "create", "update", "delete", "get",
    "want", "should", "display", "show", "see", "add", "set",
    "list", "allow", "able", "user", "system", "using", "use",
})


def _load_epics(req_path: str) -> list[dict]:
    """Parse the YAML (or YAML-in-Markdown) at *req_path* and return the epics list."""
    p = Path(req_path)
    raw_text = p.read_text(encoding="utf-8")
    if p.suffix.lower() == ".md":
        match = re.search(r"```yaml\s*\n(.*?)\n```", raw_text, re.DOTALL)
        data = yaml.safe_load(match.group(1)) if match else {}
    else:
        data = yaml.safe_load(raw_text)
    return (data or {}).get("epics", [])


def _derive_from_stories(epics: list[dict]) -> str:
    """Extract up to 3 domain-relevant words from story titles across all epics."""
    words: list[str] = []
    for ep in epics:
        for feat in ep.get("features", []):
            for story in feat.get("stories", []):
                title = (story.get("title", "") or "").strip()
                for w in re.findall(r"[a-zA-Z]{3,}", title):
                    if w.lower() not in _STOP_WORDS:
                        words.append(w.lower())
    if not words:
        return ""
    top = [w for w, _ in Counter(words).most_common(5)][:3]
    return " ".join(w.capitalize() for w in top)


def extract_project_name(req_path: str, fallback: str = "Agent OS Project") -> str:
    """Return a human-readable project name derived from the requirements file.

    Preference order:
    1. First epic title (if non-generic).
    2. Top domain words from story titles.
    3. *fallback*.
    """
    try:
        epics = _load_epics(req_path)
        if not epics:
            return fallback
        epic_title = (epics[0].get("title", "") or "").strip()
        if epic_title.lower() not in _GENERIC_TITLES:
            return epic_title
        derived = _derive_from_stories(epics)
        return derived or fallback
    except Exception:
        return fallback


def name_to_slug(name: str) -> str:
    """Convert a project name to a lowercase URL/repo slug."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
