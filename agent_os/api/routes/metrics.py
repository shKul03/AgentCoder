"""Metrics routes — aggregate pipeline metrics."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..deps import get_orchestrator
from ..schemas import MetricsResponse

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


@router.get("", response_model=MetricsResponse)
def get_metrics(orch=Depends(get_orchestrator)):
    state = orch.state_mgr.state

    all_iters = orch.db.conn.execute(
        "SELECT token_usage FROM iterations"
    ).fetchall()
    total_iterations = len(all_iters)
    total_tokens = sum(row["token_usage"] for row in all_iters)

    cost_per_1k = orch.config.budget.cost_per_1k_tokens
    total_cost = (total_tokens / 1000.0) * cost_per_1k

    return MetricsResponse(
        total_iterations=total_iterations,
        total_token_usage=total_tokens,
        pipeline_status=state.pipeline_status.value,
        total_cost=round(total_cost, 4),
    )
