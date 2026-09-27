"""
FastAPI 后端入口。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

# 把项目根目录加入 sys.path，确保能导入 editor_core / models 等模块
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from web.backend import auth
from web.backend.routers import export, platforms, projects, segments, videos, v6_export

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Video Editor AI Web",
    version="0.2.0",
    description="智能剪辑 Web UI 后端 API",
)

# 允许前端跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://clip.hhhman.top",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://192.168.1.11:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(videos.router)
app.include_router(segments.router)
app.include_router(export.router)
app.include_router(v6_export.router)
app.include_router(platforms.router, prefix="/api/platforms", tags=["platforms"])

@app.get("/api/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    open_paths = ("/api/health", "/api/auth/login", "/api/auth/logout")
    if path.startswith("/api") and not any(path.startswith(p) for p in open_paths):
        user = auth.current_user(request)
        if not user:
            return JSONResponse({"detail": "请先登录"}, status_code=401)
        request.state.user = user
    return await call_next(request)


# 挂载前端构建产物（放在最后，避免覆盖 API 路由）
frontend_dist = ROOT / "web" / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "web.backend.app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=[str(ROOT / "web" / "backend")],
    )
