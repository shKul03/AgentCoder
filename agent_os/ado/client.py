"""Azure DevOps work-item state client."""

from __future__ import annotations

import base64
import logging
from typing import Sequence
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

_ADO_API_VERSION = "7.1"


class AzureDevOpsClient:
    """Thin HTTP client for transitioning ADO work-item states."""

    def __init__(self, org: str, token: str) -> None:
        self._org_enc = quote(org, safe="")
        token_b64 = base64.b64encode(f":{token}".encode()).decode()
        self._headers = {
            "Authorization": f"Basic {token_b64}",
            "Content-Type": "application/json-patch+json",
        }

    def _patch_state(self, work_item_ids: Sequence[int], state: str) -> None:
        patch_body = [{"op": "replace", "path": "/fields/System.State", "value": state}]
        with httpx.Client(headers=self._headers, timeout=15, follow_redirects=False) as client:
            for wi_id in work_item_ids:
                try:
                    resp = client.patch(
                        f"https://dev.azure.com/{self._org_enc}/_apis/wit/workitems/{wi_id}"
                        f"?api-version={_ADO_API_VERSION}",
                        json=patch_body,
                    )
                    logger.debug("[ADO] %s work item %s → HTTP %s", state, wi_id, resp.status_code)
                except Exception:
                    logger.debug("Failed to set ADO work item %s to %s", wi_id, state, exc_info=True)

    def activate(self, work_item_ids: Sequence[int]) -> None:
        """Transition work items from New → Active."""
        if not work_item_ids:
            return
        try:
            self._patch_state(work_item_ids, "Active")
            logger.info("[ADO] Activated %d work item(s): %s", len(work_item_ids), list(work_item_ids))
        except Exception:
            logger.warning("Failed to activate ADO work items", exc_info=True)

    def close(self, work_item_ids: Sequence[int]) -> None:
        """Transition work items to Closed."""
        if not work_item_ids:
            return
        try:
            self._patch_state(work_item_ids, "Closed")
            logger.info("[ADO] Closed %d work item(s): %s", len(work_item_ids), list(work_item_ids))
        except Exception:
            logger.warning("Failed to close ADO work items", exc_info=True)
