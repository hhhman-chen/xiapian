"""
AI 分析与视频导出路由。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse

import ai_analyzer
import editor_core
from models import Project, Segment
from web.backend import dependencies as deps

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects/{project_id}", tags=["export"])


def _run_analyze_task(project_id: str) -> None:
    """在后台执行 AI 分析。"""
    deps.set_task_status(project_id, "analyze", "running", progress=10)
    try:
        project = deps.load_project(project_id)
        segments = ai_analyzer.analyze_video(project)
        project.segments = segments
        deps.save_project(project_id, project)
        deps.set_task_status(
            project_id,
            "analyze",
            "done",
            progress=100,
            result=[seg.to_dict() for seg in segments],
        )
        logger.info("AI 分析完成: %s, %d segments", project_id, len(segments))
    except Exception as exc:
        logger.error("AI 分析失败: %s", exc)
        deps.set_task_status(project_id, "analyze", "error", progress=0, error=str(exc))


def _run_export_task(project_id: str) -> None:
    """在后台执行视频导出。"""
    deps.set_task_status(project_id, "export", "running", progress=10)
    try:
        project = deps.load_project(project_id)
        output_path = deps.get_exported_video_path(project_id)

        # 先把删除片段裁剪掉，再导出
        segments_to_remove = [seg for seg in project.segments if not seg.keep]
        editor_core.cut_segments(project, segments_to_remove)

        deps.set_task_status(project_id, "export", "running", progress=40)
        final_path = editor_core.export_video(project, str(output_path))
        deps.set_task_status(
            project_id,
            "export",
            "done",
            progress=100,
            result={"output_path": final_path},
        )
        logger.info("视频导出完成: %s", final_path)
    except Exception as exc:
        logger.error("视频导出失败: %s", exc)
        deps.set_task_status(project_id, "export", "error", progress=0, error=str(exc))


@router.post("/analyze")
async def analyze(project_id: str, background_tasks: BackgroundTasks) -> dict[str, Any]:
    """启动 AI 分析后台任务。"""
    if not deps._project_dir(project_id).exists():
        raise HTTPException(status_code=404, detail="项目不存在")

    deps.set_task_status(project_id, "analyze", "pending", progress=0)
    background_tasks.add_task(_run_analyze_task, project_id)
    return {"message": "AI 分析已启动", "project_id": project_id}


@router.get("/analyze/status")
async def analyze_status(project_id: str) -> dict[str, Any]:
    """查询 AI 分析任务状态。"""
    status = deps.get_task_status(project_id)
    if status is None:
        return {"task": "analyze", "status": "idle", "progress": 0}
    return status


@router.post("/export")
async def export(project_id: str, background_tasks: BackgroundTasks) -> dict[str, Any]:
    """启动视频导出后台任务。"""
    if not deps._project_dir(project_id).exists():
        raise HTTPException(status_code=404, detail="项目不存在")

    deps.set_task_status(project_id, "export", "pending", progress=0)
    background_tasks.add_task(_run_export_task, project_id)
    return {"message": "视频导出已启动", "project_id": project_id}


@router.get("/export/status")
async def export_status(project_id: str) -> dict[str, Any]:
    """查询视频导出任务状态。"""
    status = deps.get_task_status(project_id)
    if status is None:
        return {"task": "export", "status": "idle", "progress": 0}
    return status


@router.get("/download")
async def download_export(project_id: str) -> Any:
    """下载导出后的视频。"""
    output_path = deps.get_exported_video_path(project_id)
    if not output_path.exists():
        raise HTTPException(status_code=404, detail="导出文件不存在，请先执行导出")
    return FileResponse(
        str(output_path),
        media_type="video/mp4",
        filename=f"{project_id}_edited.mp4",
    )
