"""Requirements routes — list, upload (yaml/csv/txt/xlsx), select, and remote ingest."""

from __future__ import annotations

import csv
import io
import logging
import re
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel

from ...requirements.parser import RequirementsParser
from ...requirements.schema import RequirementsDocument
from ...storage.models import PipelineStatus
from ...storage.requirement_repo import RequirementRepository
from ..deps import get_orchestrator, orch_holder
from ..schemas import RequirementResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/requirements", tags=["requirements"])

_MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MB


class RequirementsUploadResponse(BaseModel):
    success: bool
    path: str
    stats: dict[str, int] = {}
    message: str = ""


def _active_statuses() -> set[str]:
    """Pipeline statuses that indicate work is in-progress or paused at a gate."""
    return {
        s.value for s in PipelineStatus
        if s not in (PipelineStatus.IDLE, PipelineStatus.PIPELINE_COMPLETE, PipelineStatus.FAILED)
    }


def _assert_pipeline_idle(orch: Any) -> None:
    """Raise 409 if the pipeline is currently running or paused at a HITL gate."""
    current = orch.state_mgr.current_status.value
    if current in _active_statuses():
        raise HTTPException(
            status_code=409,
            detail=(
                f"Pipeline is active (state: {current}). "
                "Reset or complete the pipeline before changing requirements."
            ),
        )


def _validate_yaml_requirements(raw_bytes: bytes, filename: str) -> tuple[dict[str, int], str]:
    """Parse and validate requirements YAML bytes.

    Returns (stats_dict, error_message). On success error_message is ''.
    """
    try:
        raw = yaml.safe_load(raw_bytes.decode("utf-8", errors="replace"))
    except yaml.YAMLError as exc:
        return {}, f"Invalid YAML: {exc}"

    try:
        doc = RequirementsDocument.model_validate(raw)
    except Exception as exc:
        return {}, f"Requirements schema validation failed: {exc}"

    # Count items
    stats: dict[str, int] = {"epics": 0, "features": 0, "stories": 0, "acceptance_criteria": 0}
    for epic in doc.epics:
        stats["epics"] += 1
        for feat in epic.features:
            stats["features"] += 1
            for story in feat.stories:
                stats["stories"] += 1
                stats["acceptance_criteria"] += len(story.acceptance_criteria)

    return stats, ""


def _persist_requirements_path(orch: Any, path: str) -> None:
    """Update config.requirements.path in-memory and write to config.yaml."""
    orch.config.requirements.path = path
    try:
        from ..routes.settings import _write_config_yaml
        _write_config_yaml(orch.config, orch_holder.config_path)
        logger.info("Requirements path updated to: %s", path)
    except Exception as exc:
        logger.warning("Could not persist requirements path to config.yaml: %s", exc)


# ---------------------------------------------------------------------------
# Existing endpoint
# ---------------------------------------------------------------------------

@router.get("", response_model=list[RequirementResponse])
def list_requirements(orch=Depends(get_orchestrator)):
    repo = RequirementRepository(orch.db.conn)
    reqs = repo.get_all()
    return [
        RequirementResponse(
            id=r.id, type=r.type if isinstance(r.type, str) else r.type.value,
            parent_id=r.parent_id, title=r.title,
            description=r.description, status=r.status,
        )
        for r in reqs
    ]


@router.get("/preview")
def preview_requirements(orch=Depends(get_orchestrator)):
    """Return the active requirements YAML as structured JSON for the UI preview modal."""
    req_path = getattr(getattr(orch, "config", None), "requirements", None)
    path = getattr(req_path, "path", "") or ""
    if not path or not Path(path).exists():
        raise HTTPException(
            status_code=404,
            detail="No requirements file is currently loaded. Upload or ingest one first.",
        )
    try:
        raw = RequirementsParser._read_yaml(path)
        doc = RequirementsDocument.model_validate(raw)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not parse requirements file: {exc}")
    return doc.model_dump()


# ---------------------------------------------------------------------------
# New: upload a requirements YAML file
# ---------------------------------------------------------------------------

@router.post("/upload", response_model=RequirementsUploadResponse)
async def upload_requirements(
    file: UploadFile,
    orch=Depends(get_orchestrator),
) -> RequirementsUploadResponse:
    """Upload a local requirements YAML file and set it as the active requirements source.

    - Accepts only .yaml / .yml files up to 1 MB.
    - Validates the YAML against the RequirementsDocument schema.
    - Saves the file under the Agent OS data directory.
    - Updates config.requirements.path and persists to config.yaml.
    - Returns stats (epic/feature/story/AC counts).
    """
    _assert_pipeline_idle(orch)

    # Extension check
    fname = file.filename or "requirements.yaml"
    ext = Path(fname).suffix.lower()
    accepted = {".yaml", ".yml", ".csv", ".txt", ".xlsx"}
    if ext not in accepted:
        raise HTTPException(
            status_code=422,
            detail="Accepted formats: .yaml / .yml / .csv / .txt / .xlsx",
        )

    # Read & size-check
    content = await file.read()
    if len(content) > _MAX_FILE_BYTES:
        raise HTTPException(status_code=422, detail="File exceeds 5 MB size limit.")

    if ext in (".yaml", ".yml"):
        # --- existing YAML path ---
        # Strip BOM / zero-width chars
        _text = content.decode("utf-8-sig", errors="replace")
        _text = _text.lstrip("\u200b\u200c\u200d\ufeff")
        content = _text.encode("utf-8")

        stats, err = _validate_yaml_requirements(content, fname)
        if err:
            raise HTTPException(status_code=422, detail=err)
        if stats.get("epics", 0) == 0:
            raise HTTPException(
                status_code=422,
                detail=(
                    "No epics found in the requirements YAML. "
                    "Make sure your file has a top-level 'epics:' key with at least one entry."
                ),
            )
        yaml_bytes = content

    elif ext == ".csv":
        yaml_bytes, stats, err = _csv_to_yaml(content)
        if err:
            raise HTTPException(status_code=422, detail=err)

    elif ext == ".txt":
        yaml_bytes, stats, err = _txt_to_yaml(content, fname)
        if err:
            raise HTTPException(status_code=422, detail=err)

    elif ext == ".xlsx":
        yaml_bytes, stats, err = _xlsx_to_yaml(content)
        if err:
            raise HTTPException(status_code=422, detail=err)

    else:
        raise HTTPException(status_code=422, detail=f"Unsupported extension: {ext}")

    # Save to data/requirements/<filename>.yaml
    data_dir = orch.config.storage.data_dir
    save_dir = data_dir / "requirements"
    save_dir.mkdir(parents=True, exist_ok=True)
    safe_stem = re.sub(r"[^a-zA-Z0-9_\-]", "_", Path(fname).stem)
    dest = save_dir / f"{safe_stem}.yaml"
    dest.write_bytes(yaml_bytes)
    logger.info("Requirements file saved to: %s", dest)

    _persist_requirements_path(orch, str(dest))

    return RequirementsUploadResponse(
        success=True,
        path=str(dest),
        stats=stats,
        message=(
            f"Requirements loaded: {stats.get('epics', 0)} epics, "
            f"{stats.get('features', 0)} features, "
            f"{stats.get('stories', 0)} stories, "
            f"{stats.get('acceptance_criteria', 0)} ACs"
        ),
    )


# ---------------------------------------------------------------------------
# New: select an already-existing local requirements YAML file
# ---------------------------------------------------------------------------

class SelectRequirementsRequest(BaseModel):
    path: str


@router.post("/select", response_model=RequirementsUploadResponse)
def select_requirements(
    body: SelectRequirementsRequest,
    orch=Depends(get_orchestrator),
) -> RequirementsUploadResponse:
    """Point the pipeline at an existing local requirements YAML file.

    - The file must already exist on disk.
    - Accepts only .yaml / .yml files up to 1 MB.
    - Validates the YAML against the RequirementsDocument schema.
    - Updates config.requirements.path and persists to config.yaml.
    """
    _assert_pipeline_idle(orch)

    path = Path(body.path)
    if not path.suffix.lower() in (".yaml", ".yml"):
        raise HTTPException(status_code=422, detail="Only .yaml / .yml files are accepted.")

    if not path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {body.path}")

    if path.stat().st_size > _MAX_FILE_BYTES:
        raise HTTPException(status_code=422, detail="File exceeds 1 MB size limit.")

    content = path.read_bytes()
    stats, err = _validate_yaml_requirements(content, path.name)
    if err:
        raise HTTPException(status_code=422, detail=err)

    _persist_requirements_path(orch, str(path.resolve()))

    if not path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {body.path}")

    if path.stat().st_size > _MAX_FILE_BYTES:
        raise HTTPException(status_code=422, detail="File exceeds 5 MB size limit.")

    content = path.read_bytes()
    stats, err = _validate_yaml_requirements(content, path.name)
    if err:
        raise HTTPException(status_code=422, detail=err)

    _persist_requirements_path(orch, str(path.resolve()))

    return RequirementsUploadResponse(
        success=True,
        path=str(path.resolve()),
        stats=stats,
        message=f"Requirements loaded: {stats['epics']} epics, {stats['features']} features, "
                f"{stats['stories']} stories, {stats['acceptance_criteria']} ACs",
    )


# ---------------------------------------------------------------------------
# Helpers: convert CSV / TXT / XLSX → canonical requirements YAML bytes
# ---------------------------------------------------------------------------

_EPIC_TITLE = "Imported Requirements"
_FEAT_TITLE = "Imported Features"


def _ac_from_list(story_id: str, ac_lines: list[str], desc: str) -> list[dict]:
    """Build AcceptanceCriteria dicts from a list of strings.
    Falls back to the story description (split by sentence) if list is empty.
    Always returns at least one entry so the validator doesn't reject the story.
    """
    lines = [l.strip() for l in ac_lines if l.strip()]
    if not lines and desc:
        # Split by common sentence terminators to produce individual items
        raw = [s.strip() for s in re.split(r'(?<=[.!?])\s+|\n', desc) if s.strip()]
        lines = raw or [desc.strip()]
    if not lines:
        lines = ["Verify the feature works as described."]
    return [
        {"id": f"{story_id}-AC{i + 1}", "title": line, "description": ""}
        for i, line in enumerate(lines)
    ]


def _build_yaml_from_items(items: list[dict]) -> tuple[bytes, dict[str, int], str]:
    """Convert a flat list of {id, title, description} dicts into requirements YAML bytes."""
    if not items:
        return b"", {}, "No requirements items found in the file."

    # Group into epics → features → stories if 'type' column present, else flat hierarchy
    has_type = any("type" in i for i in items)
    if has_type:
        epics: dict[str, dict] = {}
        features: dict[str, dict] = {}
        stories: list[dict] = []
        for row in items:
            rtype = (row.get("type") or "story").lower().strip()
            rid = row.get("id") or f"auto-{len(stories) + 1}"
            rtitle = row.get("title") or row.get("name") or "(untitled)"
            rdesc = row.get("description") or row.get("desc") or ""
            rparent = row.get("parent_id") or row.get("epic_id") or ""
            if rtype in ("epic",):
                epics[rid] = {"id": rid, "title": rtitle, "description": rdesc, "features": []}
            elif rtype in ("feature", "story_group"):
                features[rid] = {
                    "id": rid, "title": rtitle, "description": rdesc,
                    "parent": rparent, "stories": [],
                }
            else:
                raw_ac = row.get("acceptance_criteria") or []
                stories.append({
                    "id": rid, "title": rtitle, "description": rdesc,
                    "parent": rparent,
                    "acceptance_criteria": _ac_from_list(rid, raw_ac, rdesc),
                })

        # Wire up relationships
        orphan_feat = {"id": "F-general", "title": "General", "description": "", "stories": []}
        orphan_epic = {"id": "E-general", "title": _EPIC_TITLE, "description": "", "features": []}

        for feat_id, feat in features.items():
            parent_ep = feat.get("parent", "")
            if parent_ep in epics:
                epics[parent_ep]["features"].append(feat)
            else:
                orphan_epic["features"].append(feat)

        for story in stories:
            parent_f = story.get("parent", "")
            placed = False
            for feat in features.values():
                if feat["id"] == parent_f:
                    feat["stories"].append(story)
                    placed = True
                    break
            if not placed:
                orphan_feat["stories"].append(story)

        if orphan_feat["stories"]:
            orphan_epic["features"].append(orphan_feat)

        if not epics:
            epics["E-general"] = orphan_epic
        else:
            for ep in epics.values():
                if not ep["features"]:
                    ep["features"].append(orphan_feat)

        final_epics = list(epics.values())
    else:
        # Simple flat structure: one epic → one feature → all rows as stories
        story_list = []
        for i, row in enumerate(items):
            sid = row.get("id") or f"S{i + 1}"
            rdesc = row.get("description") or row.get("desc") or ""
            raw_ac = row.get("acceptance_criteria") or []
            story_list.append({
                "id": sid,
                "title": row.get("title") or row.get("name") or "(untitled)",
                "description": rdesc,
                "acceptance_criteria": _ac_from_list(sid, raw_ac, rdesc),
            })
        final_epics = [{
            "id": "E1",
            "title": _EPIC_TITLE,
            "description": "Auto-imported from uploaded file.",
            "features": [{
                "id": "F1",
                "title": _FEAT_TITLE,
                "description": "",
                "stories": story_list,
            }],
        }]

    doc = {"epics": final_epics}
    yaml_bytes = yaml.dump(doc, allow_unicode=True, sort_keys=False).encode("utf-8")

    # Count stats — only count items that were actually ingested from the
    # source data, not auto-generated parent containers (E-general, F-general, E1, F1).
    if has_type:
        n_epics = sum(1 for row in items if (row.get("type") or "").lower().strip() == "epic")
        n_feat  = sum(1 for row in items if (row.get("type") or "").lower().strip() in ("feature", "story_group"))
    else:
        n_epics = 0
        n_feat  = 0
    n_stor = sum(
        len(f.get("stories", []))
        for ep in final_epics for f in ep.get("features", [])
    )
    n_ac = sum(
        len(s.get("acceptance_criteria", []))
        for ep in final_epics for f in ep.get("features", []) for s in f.get("stories", [])
    )
    stats = {"epics": n_epics, "features": n_feat, "stories": n_stor, "acceptance_criteria": n_ac}
    return yaml_bytes, stats, ""


def _csv_to_yaml(content: bytes) -> tuple[bytes, dict[str, int], str]:
    try:
        text = content.decode("utf-8-sig", errors="replace")
        # Sniff the dialect
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel

    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    # Normalise column names to lowercase
    rows = []
    for row in reader:
        rows.append({k.strip().lower(): (v or "").strip() for k, v in row.items()})

    # Map common column name variants
    _COL_MAP = {
        "summary": "title", "name": "title", "story": "title", "issue": "title",
        "desc": "description", "details": "description",
        "issuetype": "type", "issue type": "type",
        "parent": "parent_id",
    }
    normalised = []
    for row in rows:
        nr = {}
        for k, v in row.items():
            nr[_COL_MAP.get(k, k)] = v
        normalised.append(nr)

    return _build_yaml_from_items(normalised)


def _txt_to_yaml(content: bytes, fname: str) -> tuple[bytes, dict[str, int], str]:
    text = content.decode("utf-8-sig", errors="replace")
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return b"", {}, "Text file is empty."
    items = [{"id": f"S{i + 1}", "title": line, "description": ""} for i, line in enumerate(lines)]
    return _build_yaml_from_items(items)


def _xlsx_to_yaml(content: bytes) -> tuple[bytes, dict[str, int], str]:
    try:
        import openpyxl  # type: ignore[import]
    except ImportError:
        return b"", {}, (
            "openpyxl is not installed. "
            "Install it with: pip install openpyxl — or convert the file to .csv first."
        )

    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
    except Exception as exc:
        return b"", {}, f"Could not read XLSX file: {exc}"

    if not rows:
        return b"", {}, "XLSX file is empty."

    # First row = header, remaining = data
    header = [str(h or "").strip().lower() for h in rows[0]]
    _COL_MAP = {
        "summary": "title", "name": "title", "story": "title", "issue": "title",
        "desc": "description", "details": "description",
        "issuetype": "type", "issue type": "type",
        "parent": "parent_id",
    }
    items = []
    for row in rows[1:]:
        d: dict[str, str] = {}
        for col, val in zip(header, row):
            d[_COL_MAP.get(col, col)] = str(val or "").strip()
        if any(d.values()):
            items.append(d)

    return _build_yaml_from_items(items)


# ---------------------------------------------------------------------------
# Remote ingest: JIRA / Asana / ADO
# ---------------------------------------------------------------------------


class RemoteIngestRequest(BaseModel):
    source: str  # "jira" | "asana" | "ado"
    # JIRA
    jira_url: str = ""
    jira_email: str = ""
    jira_api_token: str = ""
    jira_project_key: str = ""
    # Asana
    asana_token: str = ""
    asana_project_id: str = ""
    # ADO
    ado_org: str = ""
    ado_token: str = ""
    ado_project: str = ""


class RemoteValidationResult(BaseModel):
    valid: bool
    errors: list[str] = []
    warnings: list[str] = []


@router.post("/validate-remote", response_model=RemoteValidationResult)
async def validate_remote_connection(body: RemoteIngestRequest, orch=Depends(get_orchestrator)) -> RemoteValidationResult:
    """Test connectivity and credentials for a remote source without ingesting."""
    import httpx

    source = (body.source or "").lower()
    errors: list[str] = []
    warnings: list[str] = []

    if source == "jira":
        if not body.jira_url:
            errors.append("JIRA URL is required.")
        if not body.jira_email:
            errors.append("JIRA email is required.")
        if not body.jira_api_token:
            errors.append("JIRA API token is required.")
        if not body.jira_project_key:
            errors.append("JIRA project key is required.")
        if errors:
            return RemoteValidationResult(valid=False, errors=errors)

        base = body.jira_url.rstrip("/")
        auth = (body.jira_email, body.jira_api_token)
        try:
            async with httpx.AsyncClient(auth=auth, headers={"Accept": "application/json"}, timeout=15) as client:
                resp = await client.get(f"{base}/rest/api/3/myself")
                if resp.status_code == 401:
                    errors.append("Authentication failed. Check your email and API token.")
                elif not resp.is_success:
                    errors.append(f"Failed to connect to JIRA: HTTP {resp.status_code}")
                else:
                    # Verify project exists
                    proj_resp = await client.get(f"{base}/rest/api/3/project/{body.jira_project_key}")
                    if proj_resp.status_code == 404:
                        errors.append(f"Project '{body.jira_project_key}' not found in JIRA.")
                    elif not proj_resp.is_success:
                        warnings.append(f"Could not verify project key: HTTP {proj_resp.status_code}")
        except httpx.ConnectError:
            errors.append(f"Cannot connect to JIRA at '{base}'. Check the URL.")
        except httpx.TimeoutException:
            errors.append("Connection to JIRA timed out.")

    elif source == "asana":
        if not body.asana_token:
            errors.append("Asana Personal Access Token is required.")
        if not body.asana_project_id:
            errors.append("Asana project ID is required.")
        if errors:
            return RemoteValidationResult(valid=False, errors=errors)

        headers = {"Authorization": f"Bearer {body.asana_token}", "Accept": "application/json"}
        try:
            async with httpx.AsyncClient(headers=headers, timeout=15) as client:
                resp = await client.get("https://app.asana.com/api/1.0/users/me")
                if resp.status_code == 401:
                    errors.append("Authentication failed. Check your Personal Access Token.")
                elif not resp.is_success:
                    errors.append(f"Failed to connect to Asana: HTTP {resp.status_code}")
                else:
                    proj_resp = await client.get(f"https://app.asana.com/api/1.0/projects/{body.asana_project_id}")
                    if proj_resp.status_code == 404:
                        errors.append(f"Project '{body.asana_project_id}' not found in Asana.")
                    elif not proj_resp.is_success:
                        warnings.append(f"Could not verify project: HTTP {proj_resp.status_code}")
        except httpx.ConnectError:
            errors.append("Cannot connect to Asana API.")
        except httpx.TimeoutException:
            errors.append("Connection to Asana timed out.")

    elif source == "ado":
        from urllib.parse import quote
        import base64 as b64

        # Fall back to saved config credentials so validation works even when
        # the UI is showing the masked placeholder.
        cfg_req = getattr(getattr(orch, "config", None), "requirements", None)
        ado_org = body.ado_org or getattr(cfg_req, "ado_org", "")
        raw_tok = body.ado_token
        ado_token = (
            raw_tok
            if raw_tok and not raw_tok.startswith("***")
            else getattr(cfg_req, "ado_token", "")
        )
        ado_project = body.ado_project or getattr(cfg_req, "ado_project", "")

        if not ado_org:
            errors.append("ADO organization name is required.")
        if not ado_token:
            errors.append("ADO Personal Access Token is required.")
        if not ado_project:
            errors.append("ADO project name is required.")
        if errors:
            return RemoteValidationResult(valid=False, errors=errors)

        token_b64 = b64.b64encode(f":{ado_token}".encode()).decode()
        headers = {"Authorization": f"Basic {token_b64}", "Content-Type": "application/json"}
        org_enc = quote(ado_org, safe="")
        try:
            async with httpx.AsyncClient(headers=headers, timeout=15, follow_redirects=False) as client:
                # Verify org + auth by listing projects
                resp = await client.get(f"https://dev.azure.com/{org_enc}/_apis/projects?api-version=7.1")
                if resp.status_code in (301, 302, 303, 307, 308, 401):
                    errors.append("Authentication failed. Check your PAT token and org name.")
                elif not resp.is_success:
                    errors.append(f"Failed to connect to ADO: HTTP {resp.status_code}")
                else:
                    # Verify project exists in org
                    projects = resp.json().get("value", [])
                    project_names = [p.get("name", "").lower() for p in projects]
                    if ado_project.lower() not in project_names:
                        errors.append(
                            f"Project '{ado_project}' not found in org '{ado_org}'. "
                            f"Available: {', '.join(p.get('name', '') for p in projects[:10])}"
                        )
        except httpx.ConnectError:
            errors.append(f"Cannot connect to Azure DevOps for org '{ado_org}'.")
        except httpx.TimeoutException:
            errors.append("Connection to Azure DevOps timed out.")

    else:
        errors.append(f"Unknown source: '{body.source}'. Use jira, asana, or ado.")

    return RemoteValidationResult(valid=len(errors) == 0, errors=errors, warnings=warnings)


def _build_requirements_md(yaml_bytes: bytes, source: str, stats: dict) -> str:
    """Wrap YAML bytes in a Markdown document for human-readable storage."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts = []
    if stats.get("epics"):
        parts.append(f"{stats['epics']} epics")
    if stats.get("features"):
        parts.append(f"{stats['features']} features")
    if stats.get("stories"):
        parts.append(f"{stats['stories']} stories")
    if stats.get("acceptance_criteria"):
        parts.append(f"{stats['acceptance_criteria']} ACs")
    summary = " · ".join(parts) if parts else "no items"

    yaml_text = yaml_bytes.decode("utf-8", errors="replace")
    return (
        f"# Agent OS Requirements\n\n"
        f"> Imported from **{source.upper()}** on {now}  \n"
        f"> {summary}\n\n"
        f"## Requirements Data\n\n"
        f"```yaml\n"
        f"{yaml_text}"
        f"```\n"
    )


def _items_to_yaml_and_save(
    items: list[dict],
    source: str,
    orch: Any,
) -> RequirementsUploadResponse:
    yaml_bytes, stats, err = _build_yaml_from_items(items)
    if err:
        raise HTTPException(status_code=422, detail=err)

    data_dir = orch.config.storage.data_dir
    save_dir = data_dir / "requirements"
    save_dir.mkdir(parents=True, exist_ok=True)
    dest = save_dir / f"requirements_from_{source}.md"
    dest.write_text(_build_requirements_md(yaml_bytes, source, stats), encoding="utf-8")
    _persist_requirements_path(orch, str(dest))

    # Build a human-friendly message that only mentions hierarchy levels
    # that were actually ingested (not auto-generated containers).
    parts = []
    if stats.get("epics"):
        parts.append(f"{stats['epics']} epics")
    if stats.get("features"):
        parts.append(f"{stats['features']} features")
    n_stories = stats.get("stories", 0)
    detail = f" ({', '.join(parts)})" if parts else ""

    return RequirementsUploadResponse(
        success=True,
        path=str(dest),
        stats=stats,
        message=f"Imported {n_stories} requirements from {source.upper()}{detail}",
    )


async def _ingest_jira(body: RemoteIngestRequest, orch: Any) -> RequirementsUploadResponse:
    import httpx  # already in API deps

    if not all([body.jira_url, body.jira_email, body.jira_api_token, body.jira_project_key]):
        raise HTTPException(
            status_code=422,
            detail="JIRA requires: jira_url, jira_email, jira_api_token, jira_project_key",
        )

    base = body.jira_url.rstrip("/")
    auth = (body.jira_email, body.jira_api_token)
    headers = {"Accept": "application/json"}

    # Fetch issues using JIRA REST API v3 JQL search
    issues: list[dict] = []
    start = 0
    max_results = 100
    async with httpx.AsyncClient(auth=auth, headers=headers, timeout=30) as client:
        while True:
            resp = await client.get(
                f"{base}/rest/api/3/search",
                params={
                    "jql": f"project = {body.jira_project_key} ORDER BY created ASC",
                    "startAt": start,
                    "maxResults": max_results,
                    "fields": "summary,description,issuetype,parent,customfield_10014",
                },
            )
            if resp.status_code == 401:
                raise HTTPException(status_code=401, detail="JIRA authentication failed. Check email and API token.")
            if not resp.is_success:
                raise HTTPException(
                    status_code=502,
                    detail=f"JIRA API error {resp.status_code}: {resp.text[:300]}",
                )
            data = resp.json()
            for issue in data.get("issues", []):
                fields = issue.get("fields", {})
                itype = (fields.get("issuetype") or {}).get("name", "story").lower()
                parent_key = ""
                if "parent" in fields and fields["parent"]:
                    parent_key = fields["parent"].get("key", "")
                issues.append({
                    "id": issue["key"],
                    "type": "epic" if "epic" in itype else ("feature" if "story" in itype else "story"),
                    "title": fields.get("summary", ""),
                    "description": _extract_jira_description(fields.get("description")),
                    "parent_id": parent_key,
                })
            total = data.get("total", 0)
            start += len(data.get("issues", []))
            if start >= total:
                break

    return _items_to_yaml_and_save(issues, "jira", orch)


def _extract_jira_description(desc: Any) -> str:
    """Extract plain text from JIRA Atlassian Document Format (ADF) or plain string."""
    if not desc:
        return ""
    if isinstance(desc, str):
        return desc
    # ADF format: {"type": "doc", "content": [...]}
    if isinstance(desc, dict) and "content" in desc:
        parts: list[str] = []
        def _walk(node: Any) -> None:
            if isinstance(node, dict):
                if node.get("type") == "text":
                    parts.append(node.get("text", ""))
                for child in node.get("content", []):
                    _walk(child)
            elif isinstance(node, list):
                for item in node:
                    _walk(item)
        _walk(desc)
        return " ".join(p for p in parts if p)
    return str(desc)


async def _ingest_asana(body: RemoteIngestRequest, orch: Any) -> RequirementsUploadResponse:
    import httpx

    if not all([body.asana_token, body.asana_project_id]):
        raise HTTPException(status_code=422, detail="Asana requires: asana_token, asana_project_id")

    headers = {
        "Authorization": f"Bearer {body.asana_token}",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        resp = await client.get(
            "https://app.asana.com/api/1.0/tasks",
            params={
                "project": body.asana_project_id,
                "opt_fields": "gid,name,notes,completed,parent,resource_subtype",
                "limit": 100,
            },
        )
        if resp.status_code == 401:
            raise HTTPException(status_code=401, detail="Asana authentication failed. Check your Personal Access Token.")
        if not resp.is_success:
            raise HTTPException(status_code=502, detail=f"Asana API error {resp.status_code}: {resp.text[:300]}")
        tasks = resp.json().get("data", [])

    items = []
    for task in tasks:
        if task.get("completed"):
            continue
        parent = task.get("parent") or {}
        items.append({
            "id": task["gid"],
            "title": task.get("name", ""),
            "description": task.get("notes", ""),
            "type": "story",
            "parent_id": parent.get("gid", "") if parent else "",
        })

    return _items_to_yaml_and_save(items, "asana", orch)


def _parse_ado_ac(raw: str | None) -> list[str]:
    """Convert ADO HTML acceptance-criteria into a plain list of strings."""
    if not raw:
        return []
    # Normalise <br> and </p> → newline, strip every other tag
    text = re.sub(r"<br\s*/?>|</p>|</li>", "\n", raw, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    lines = [l.strip().lstrip("#-*•··").strip() for l in text.splitlines()]
    return [l for l in lines if l]


async def _ingest_ado(body: RemoteIngestRequest, orch: Any) -> RequirementsUploadResponse:
    import base64
    import httpx
    from urllib.parse import quote

    # Fall back to saved config values so the user doesn't have to re-enter the PAT
    cfg_req = getattr(getattr(orch, "config", None), "requirements", None)
    ado_org     = body.ado_org     or getattr(cfg_req, "ado_org", "")
    ado_token   = body.ado_token   or getattr(cfg_req, "ado_token", "")
    ado_project = body.ado_project or getattr(cfg_req, "ado_project", "")

    if not all([ado_org, ado_token, ado_project]):
        raise HTTPException(status_code=422, detail="ADO requires: ado_org, ado_token, ado_project")

    token_b64 = base64.b64encode(f":{ado_token}".encode()).decode()
    headers = {
        "Authorization": f"Basic {token_b64}",
        "Content-Type": "application/json",
    }
    # URL-encode org and project names to handle spaces and special characters
    org_enc = quote(ado_org, safe="")
    project_enc = quote(ado_project, safe="")
    base = f"https://dev.azure.com/{org_enc}/{project_enc}/_apis"

    async with httpx.AsyncClient(
        headers=headers,
        timeout=30,
        follow_redirects=False,  # an unexpected redirect means auth failed
    ) as client:
        # Run a WIQL query to get all work items
        wiql_resp = await client.post(
            f"{base}/wit/wiql?api-version=7.1",
            json={"query": "SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project ORDER BY [System.Id]"},
        )
        # ADO returns 302 (redirect to login page) or 401 when the PAT is invalid
        if wiql_resp.status_code in (301, 302, 303, 307, 308, 401):
            raise HTTPException(
                status_code=401,
                detail=(
                    "ADO authentication failed. "
                    "Verify your PAT token has 'Work Items (Read)' scope and that "
                    f"the org '{ado_org}' and project '{ado_project}' are correct."
                ),
            )
        if not wiql_resp.is_success:
            raise HTTPException(status_code=502, detail=f"ADO WIQL error {wiql_resp.status_code}: {wiql_resp.text[:300]}")

        work_item_refs = wiql_resp.json().get("workItems", [])
        if not work_item_refs:
            raise HTTPException(status_code=404, detail="No work items found in ADO project.")

        ids = ",".join(str(wi["id"]) for wi in work_item_refs[:200])
        _ADO_FIELDS = (
            "System.Id,System.Title,System.Description,"
            "System.WorkItemType,System.Parent,"
            "Microsoft.VSTS.Common.AcceptanceCriteria"
        )
        detail_resp = await client.get(
            f"{base}/wit/workitems?ids={ids}&fields={_ADO_FIELDS}&api-version=7.1",
        )
        if not detail_resp.is_success:
            raise HTTPException(status_code=502, detail=f"ADO work items error {detail_resp.status_code}")
        work_items = detail_resp.json().get("value", [])

    items = []
    work_item_ids: list[int] = []
    for wi in work_items:
        fields = wi.get("fields", {})
        wi_id = fields.get("System.Id", 0)
        wi_type = fields.get("System.WorkItemType", "Story").lower()
        parent_id = str(fields.get("System.Parent", "") or "")
        ac_lines = _parse_ado_ac(fields.get("Microsoft.VSTS.Common.AcceptanceCriteria"))
        items.append({
            "id": str(wi_id),
            "title": fields.get("System.Title", ""),
            "description": re.sub(r"<[^>]+>", "", fields.get("System.Description") or ""),
            "type": "epic" if "epic" in wi_type else ("feature" if "feature" in wi_type else "story"),
            "parent_id": parent_id,
            "acceptance_criteria": ac_lines,
        })
        if wi_id:
            work_item_ids.append(int(wi_id))

    result = _items_to_yaml_and_save(items, "ado", orch)

    # Store ADO work item IDs and credentials in orchestrator metadata for later state updates
    if hasattr(orch, "state_mgr"):
        orch.state_mgr.update_metadata({
            "ado_work_item_ids": work_item_ids,
            "ado_org": ado_org,
            "ado_project": ado_project,
            "ado_token": ado_token,
        })

    return result


async def _update_ado_work_item_states(
    ado_org: str,
    ado_token: str,
    work_item_ids: list[int],
    target_state: str,
) -> None:
    """Update the System.State field of ADO work items via PATCH."""
    import base64 as b64
    import httpx
    from urllib.parse import quote

    if not work_item_ids:
        return

    token_b64 = b64.b64encode(f":{ado_token}".encode()).decode()
    headers = {
        "Authorization": f"Basic {token_b64}",
        "Content-Type": "application/json-patch+json",
    }
    org_enc = quote(ado_org, safe="")
    patch_body = [
        {"op": "replace", "path": "/fields/System.State", "value": target_state}
    ]

    async with httpx.AsyncClient(headers=headers, timeout=15, follow_redirects=False) as client:
        for wi_id in work_item_ids:
            try:
                resp = await client.patch(
                    f"https://dev.azure.com/{org_enc}/_apis/wit/workitems/{wi_id}?api-version=7.1",
                    json=patch_body,
                )
                if not resp.is_success:
                    logger.warning(
                        "Failed to update work item %d to %s: %d %s",
                        wi_id, target_state, resp.status_code, resp.text[:200],
                    )
            except Exception:
                logger.debug("Error updating work item %d", wi_id, exc_info=True)


class AdoStateUpdateRequest(BaseModel):
    target_state: str = "Closed"


@router.post("/ado-update-states")
async def update_ado_work_item_states(
    body: AdoStateUpdateRequest,
    orch=Depends(get_orchestrator),
) -> dict:
    """Update the state of all ADO work items ingested in this pipeline run.

    Typically called when the pipeline completes to transition items to Closed.
    """
    meta = orch.state_mgr.state.metadata if hasattr(orch, "state_mgr") else {}
    work_item_ids = meta.get("ado_work_item_ids", [])
    ado_org = meta.get("ado_org", "")
    ado_token = meta.get("ado_token", "")

    if not work_item_ids or not ado_org or not ado_token:
        raise HTTPException(
            status_code=422,
            detail="No ADO work item IDs or credentials found in pipeline metadata. "
                   "Ensure requirements were ingested from ADO.",
        )

    await _update_ado_work_item_states(ado_org, ado_token, work_item_ids, body.target_state)
    return {"updated": len(work_item_ids), "target_state": body.target_state}


class AdoProjectsRequest(BaseModel):
    org: str
    token: str


@router.post("/ado-projects")
async def get_ado_projects(body: AdoProjectsRequest, orch=Depends(get_orchestrator)) -> dict:
    """Fetch all project names from an Azure DevOps organisation.

    Accepts the ADO organisation name and a Personal Access Token (PAT).
    When the UI sends the masked placeholder (*** or empty) the saved
    config token is used automatically so the user never has to re-enter it.
    """
    import base64 as _b64
    import httpx
    from urllib.parse import quote

    # Fall back to the saved config value so the user does not have to
    # re-enter the PAT after it has been saved once.
    cfg_req = getattr(getattr(orch, "config", None), "requirements", None)
    org   = body.org or getattr(cfg_req, "ado_org", "")
    token = (
        body.token
        if body.token and not body.token.startswith("***")
        else getattr(cfg_req, "ado_token", "")
    )

    if not org or not token:
        raise HTTPException(status_code=422, detail="Both 'org' and 'token' are required.")

    token_b64 = _b64.b64encode(f":{token}".encode()).decode()
    headers = {
        "Authorization": f"Basic {token_b64}",
        "Accept": "application/json",
    }
    org_enc = quote(org, safe="")
    url = f"https://dev.azure.com/{org_enc}/_apis/projects?api-version=7.1"

    try:
        async with httpx.AsyncClient(headers=headers, timeout=15, follow_redirects=False) as client:
            resp = await client.get(url)
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail=f"Cannot connect to Azure DevOps for org '{org}'.")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Connection to Azure DevOps timed out.")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Request failed: {exc}")

    if resp.status_code == 401:
        raise HTTPException(status_code=401, detail="Invalid Personal Access Token or insufficient permissions.")
    if not resp.is_success:
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"Azure DevOps API error: {resp.status_code} {resp.text[:200]}",
        )

    try:
        data = resp.json()
        projects = [p["name"] for p in data.get("value", []) if p.get("name")]
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to parse ADO response: {exc}")

    return {"projects": projects}


@router.post("/ingest-remote", response_model=RequirementsUploadResponse)
async def ingest_remote_requirements(
    body: RemoteIngestRequest,
    orch=Depends(get_orchestrator),
) -> RequirementsUploadResponse:
    """Ingest requirements from a remote source (JIRA / Asana / ADO).

    Fetches work items from the configured tool, converts them to the internal
    YAML requirements format, saves to disk (as requirements_from_<source>.yaml),
    and sets it as the active requirements file.
    """
    _assert_pipeline_idle(orch)
    source = (body.source or "").lower()

    if source == "jira":
        return await _ingest_jira(body, orch)
    elif source == "asana":
        return await _ingest_asana(body, orch)
    elif source == "ado":
        return await _ingest_ado(body, orch)
    else:
        raise HTTPException(status_code=422, detail=f"Unknown source: {body.source}. Use jira, asana, or ado.")

