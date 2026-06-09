"""Validate requirements structure before storage."""

from __future__ import annotations

from .schema import RequirementsDocument


def validate_requirements(doc: RequirementsDocument) -> list[str]:
    """Return a list of validation error strings. Empty list means valid."""
    errors: list[str] = []

    if not doc.epics:
        errors.append("No epics found in requirements document.")
        return errors

    seen_ids: set[str] = set()
    for epic in doc.epics:
        _check_dup(epic.id, seen_ids, errors, "Epic")
        if not epic.features:
            errors.append(f"Epic '{epic.id}' has no features.")
        for feature in epic.features:
            _check_dup(feature.id, seen_ids, errors, "Feature")
            if not feature.stories:
                errors.append(f"Feature '{feature.id}' has no stories.")
            for story in feature.stories:
                _check_dup(story.id, seen_ids, errors, "Story")
                if not story.acceptance_criteria:
                    errors.append(
                        f"Story '{story.id}' has no acceptance criteria."
                    )
                for ac in story.acceptance_criteria:
                    _check_dup(ac.id, seen_ids, errors, "AC")

    return errors


def _check_dup(
    req_id: str, seen: set[str], errors: list[str], kind: str
) -> None:
    if req_id in seen:
        errors.append(f"Duplicate {kind} id: '{req_id}'.")
    seen.add(req_id)
