"""
功能：公众门户 FastAPI 入口。

输入：环境变量 PORTAL_CORS_ORIGINS（逗号分隔，默认 localhost:3000）。
输出：/api/* REST；/docs OpenAPI。
上下游：web/ Next.js 通过 NEXT_PUBLIC_API_URL 调用；复用 core/services 数据层。
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import analysis, health, incidents, monitoring, stats, systems, tracks

load_dotenv(override=True)

_cors_raw = os.getenv(
    "PORTAL_CORS_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001",
)
_cors_origins = [o.strip() for o in _cors_raw.split(",") if o.strip()]

app = FastAPI(
    title="AI 治理监测门户 API",
    description="面向 web/ 展示层的只读 REST 接口，复用现有 MySQL 数据层。",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(stats.router, prefix="/api")
app.include_router(systems.router, prefix="/api")
app.include_router(monitoring.router, prefix="/api")
app.include_router(incidents.router, prefix="/api")
app.include_router(tracks.router, prefix="/api")
app.include_router(analysis.router, prefix="/api")
