"""
Web 后端共享依赖：项目目录、Project 加载/保存、任务状态存储。
"""
from __future__ import annotations

import json
import logging
import shutil
import uuid
from pathlib import Path
from typing import Any, Optional

from models import Project

logger = logging.getLogger(__name__)

# 项目持久化根目录
DATA_DIR: Path = Path(__file__).resolve().parent.parent.parent / "projects"
DATA_DIR.mkdir(parents=True, exist_ok=True)

PROJECT_JSON: str = "project.json"
SOURCE_VIDEO: str = "source_video.mp4"
EXPORTED_VIDEO: str = "output.mp4"

# 内存任务状态：{project_id: {"task": "analyze|export", "status": "pending|running|done|error", "progress": 0-100, "result": ..., "error": ...}}
TASK_STATUS: dict[str, dict[str, Any]] = {}


def _project_dir(project_id: str) -> Path:
    return DATA_DIR / project_id


def _project_json_path(project_id: str) -> Path:
    return _project_dir(project_id) / PROJECT_JSON


def _source_video_path(project_id: str) -> Path:
    return _project_dir(project_id) / SOURCE_VIDEO


def _exported_video_path(project_id: str) -> Path:
    return _project_dir(project_id) / EXPORTED_VIDEO


def list_project_ids() -> list[str]:
    """返回所有已创建的项目 ID 列表。"""
    ids: list[str] = []
    if not DATA_DIR.exists():
        return ids
    for p in DATA_DIR.iterdir():
        if p.is_dir() and (p / PROJECT_JSON).exists():
            ids.append(p.name)
    return sorted(ids)


def load_project(project_id: str) -> Project:
    """从 JSON 文件加载项目。"""
    path = _project_json_path(project_id)
    if not path.exists():
        raise FileNotFoundError(f"项目不存在: {project_id}")
    return Project.from_json(path.read_text(encoding="utf-8"))


def save_project(project_id: str, project: Project) -> None:
    """将项目保存为 JSON 文件。"""
    path = _project_json_path(project_id)
    path.write_text(project.to_json(), encoding="utf-8")


def create_project(video_path: Path, original_filename: Optional[str] = None, owner: str = "") -> str:
    """
    创建新项目目录，将上传的视频复制为 source_video.mp4，
    并初始化 Project 对象。
    """
    project_id = uuid.uuid4().hex[:12]
    project_dir = _project_dir(project_id)
    project_dir.mkdir(parents=True, exist_ok=True)

    dest_video = _source_video_path(project_id)
    shutil.copy2(str(video_path), str(dest_video))

    # 加载视频元数据
    from editor_core import load_video

    project = load_video(str(dest_video))
    project.owner = owner or ""
    project.original_filename = original_filename or ""
    save_project(project_id, project)

    logger.info("创建项目: %s, video=%s", project_id, dest_video)
    return project_id


def delete_project(project_id: str) -> bool:
    """删除项目及其目录。"""
    project_dir = _project_dir(project_id)
    if not project_dir.exists():
        return False
    shutil.rmtree(project_dir)
    TASK_STATUS.pop(project_id, None)
    return True


def get_source_video_path(project_id: str) -> Path:
    """返回原始视频文件路径。"""
    return _source_video_path(project_id)


def get_exported_video_path(project_id: str) -> Path:
    """返回导出视频文件路径。"""
    return _exported_video_path(project_id)


def get_task_status(project_id: str) -> Optional[dict[str, Any]]:
    """读取任务状态；没有则返回 None。"""
    return TASK_STATUS.get(project_id)


def set_task_status(
    project_id: str,
    task: str,
    status: str,
    progress: int = 0,
    result: Any = None,
    error: Optional[str] = None,
) -> None:
    """更新任务状态。"""
    TASK_STATUS[project_id] = {
        "task": task,
        "status": status,
        "progress": progress,
        "result": result,
        "error": error,
    }
