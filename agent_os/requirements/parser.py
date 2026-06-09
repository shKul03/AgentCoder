"""Parse requirements YAML and store in the database."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from ..storage.database import Database
from ..storage.models import RequirementRecord, RequirementType
from ..storage.requirement_repo import RequirementRepository
from .schema import RequirementsDocument
from .validator import validate_requirements

logger = logging.getLogger(__name__)


class RequirementsParser:
    """Load a requirements YAML file, validate, and persist records."""

    def __init__(self, db: Database) -> None:
        self._repo = RequirementRepository(db.conn)

    def load_and_store(self, path: str) -> dict[str, int]:
        """Parse *path*, validate, store all records, return counts."""
        raw = self._read_yaml(path)
        doc = RequirementsDocument.model_validate(raw)

        errors = validate_requirements(doc)
        if errors:
            msg = "Requirements validation failed:\n" + "\n".join(
                f"  - {e}" for e in errors
            )
            raise ValueError(msg)

        counts = {"epics": 0, "features": 0, "stories": 0, "acceptance_criteria": 0}
        for epic in doc.epics:
            self._store(epic.id, RequirementType.EPIC, None, epic.title, epic.description)
            counts["epics"] += 1
            for feat in epic.features:
                self._store(feat.id, RequirementType.FEATURE, epic.id, feat.title, feat.description)
                counts["features"] += 1
                for story in feat.stories:
                    self._store(story.id, RequirementType.STORY, feat.id, story.title, story.description)
                    counts["stories"] += 1
                    for ac in story.acceptance_criteria:
                        self._store(ac.id, RequirementType.ACCEPTANCE_CRITERIA, story.id, ac.title, ac.description)
                        counts["acceptance_criteria"] += 1

        logger.info("Stored %s requirements records.", sum(counts.values()))
        return counts

    # ------------------------------------------------------------------

    @staticmethod
    def _read_yaml(path: str) -> dict:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Requirements file not found: {path}")

        if p.suffix.lower() == ".md":
            import re as _re
            text = p.read_text(encoding="utf-8")
            match = _re.search(r"```yaml\s*\n(.*?)\n```", text, _re.DOTALL)
            if not match:
                raise ValueError(
                    f"No YAML code block found in requirements file: {path}"
                )
            data = yaml.safe_load(match.group(1))
        else:
            with p.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)

        if not isinstance(data, dict):
            raise ValueError(f"Requirements file must be a YAML mapping, got {type(data).__name__}")
        return data

    def _store(
        self,
        req_id: str,
        req_type: RequirementType,
        parent_id: str | None,
        title: str,
        description: str,
    ) -> None:
        record = RequirementRecord(
            id=req_id,
            type=req_type,
            parent_id=parent_id,
            title=title,
            description=description,
        )
        self._repo.upsert(record)
