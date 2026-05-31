"""
功能：健康检查与根信息。

输入：无。
输出：JSON 状态。
上下游：api.main 挂载。
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    """服务存活探测。"""
    return {"status": "ok", "service": "ai-safety-portal-api"}


@router.get("/")
def root() -> dict:
    """API 根路径说明。"""
    return {
        "message": "AI 治理监测门户 API",
        "docs": "/docs",
        "endpoints": [
            "/api/health",
            "/api/stats",
            "/api/systems",
            "/api/incidents/latest",
            "/api/incidents",
            "/api/tracks/policy",
            "/api/tracks/meetings",
            "/api/tracks/literature",
        ],
    }
