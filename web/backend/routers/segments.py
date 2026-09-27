"""
片段调整辅助路由（前端也可以直接保存整个项目，这里提供便捷接口）。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from models import Segment
from web.backend import dependencies as deps

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects/{project_id}/segments", tags=["segments"])


@router.get("/")
async def list_segments(project_id: str) -> dict[str, Any]:
    """获取项目片段列表。"""
    try:
        project = deps.load_project(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {
        "segments": [seg.to_dict() for seg in project.segments],
    }


@router.post("/")
async def replace_segments(project_id: str, payload: list[dict[str, Any]]) -> dict[str, Any]:
    """用新的片段列表整体替换项目现有片段。"""
    try:
        project = deps.load_project(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    try:
        project.segments = [Segment.from_dict(s) for s in payload]
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"片段数据无效: {exc}")

    deps.save_project(project_id, project)
    return {"message": "片段已更新", "segments_count": len(project.segments)}


@router.patch("/{index}")
async def update_segment(
    project_id: str,
    index: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """局部更新单个片段（keep/label/start_time/end_time/confidence）。"""
    try:
        project = deps.load_project(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if index < 0 or index >= len(project.segments):
        raise HTTPException(status_code=404, detail="片段索引越界")

    seg = project.segments[index]
    if "keep" in payload:
        seg.keep = bool(payload["keep"])
    if "label" in payload:
        seg.label = str(payload["label"])
    if "start_time" in payload:
        seg.start_time = float(payload["start_time"])
    if "end_time" in payload:
        seg.end_time = float(payload["end_time"])
    if "confidence" in payload:
        seg.confidence = float(payload["confidence"])

    deps.save_project(project_id, project)
    return {"message": "片段已更新", "segment": seg.to_dict()}
