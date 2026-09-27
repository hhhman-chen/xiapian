"""
视频流与帧预览路由。
"""
from __future__ import annotations

import io
import logging
from typing import Any, Optional

import cv2
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse

from editor_core import preview_frame
from web.backend import dependencies as deps

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects/{project_id}", tags=["videos"])


@router.get("/video")
async def stream_video(project_id: str) -> Any:
    """以支持 HTTP Range 的方式流式播放原始视频。"""
    video_path = deps.get_source_video_path(project_id)
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="视频文件不存在")
    return FileResponse(
        str(video_path),
        media_type="video/mp4",
        filename="source_video.mp4",
    )


@router.get("/frame")
async def preview_frame_image(
    project_id: str,
    t: float = Query(..., description="时间点（秒）"),
    width: Optional[int] = Query(None, description="目标宽度，不指定则原始尺寸"),
) -> StreamingResponse:
    """返回指定时间点的视频帧 JPEG 图像。"""
    try:
        project = deps.load_project(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    try:
        frame = preview_frame(project, t)
    except Exception as exc:
        logger.error("预览帧失败: project=%s t=%s error=%s", project_id, t, exc)
        raise HTTPException(status_code=500, detail=f"预览帧失败: {exc}")

    if width and width > 0:
        h, w = frame.shape[:2]
        scale = width / w
        new_h = int(h * scale)
        frame = cv2.resize(frame, (width, new_h), interpolation=cv2.INTER_AREA)

    success, encoded = cv2.imencode(".jpg", frame)
    if not success:
        raise HTTPException(status_code=500, detail="JPEG 编码失败")

    buf = io.BytesIO(encoded.tobytes())
    return StreamingResponse(buf, media_type="image/jpeg")
