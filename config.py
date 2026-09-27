"""
AI Video Editor Desktop Configuration Module
=============================================
Provides centralized configuration for the AI video editor desktop application.

Supports both Windows and Linux platforms with automatic path adaptation.
Configuration values can be overridden via environment variables.

主要配置项:
    - Ollama API 地址与模型名称
    - 输出目录、临时目录、项目目录
    - 日志级别与缓存开关
    - 分辨率、帧率等视频默认参数

平台适配:
    - Windows: 使用 %USERPROFILE%\\Videos\\VideoEditorProjects
    - Linux:   使用 ~/Videos/VideoEditorProjects

环境变量覆盖:
    - OLLAMA_API_URL       → Ollama API 地址
    - OLLAMA_MODEL_NAME    → Ollama 模型名称
    - OUTPUT_DIR           → 输出目录
    - TEMP_DIR             → 临时目录
    - PROJECT_DIR          → 项目目录
    - LOG_LEVEL            → 日志级别 (INFO/DEBUG/WARNING/ERROR)
    - ENABLE_CACHE         → 启用缓存 (true/false)
    - DEFAULT_WIDTH        → 默认输出宽度
    - DEFAULT_HEIGHT       → 默认输出高度
    - DEFAULT_FPS          → 默认帧率

示例用法:
    >>> from config import OUTPUT_DIR, OLLAMA_API_URL, OLLAMA_MODEL_NAME
    >>> print(f"输出目录: {OUTPUT_DIR}")
    输出目录: /home/chenkangye/video-editor/output
"""

from __future__ import annotations

import os
import sys
import platform
import logging
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _parse_int_env(name: str, default: int) -> int:
    """安全解析整数环境变量，解析失败返回默认值。"""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# 常量（全部大写）
# ---------------------------------------------------------------------------

# ─── AI / API 配置 ─────────────────────────────────────────────────────────

# DGX Spark Ollama API 地址（可通过环境变量 OLLAMA_API_URL 覆盖）
OLLAMA_API_URL: str = os.environ.get(
    "OLLAMA_API_URL",
    "http://127.0.0.1:11434",
)

# Ollama 模型名称（可通过环境变量 OLLAMA_MODEL_NAME 覆盖）
OLLAMA_MODEL_NAME: str = os.environ.get(
    "OLLAMA_MODEL_NAME",
    "qwen3-coder:480b-cloud",
)

# ─── 路径配置 ──────────────────────────────────────────────────────────────

# 项目根目录（config.py 所在目录）
PROJECT_ROOT: Path = Path(__file__).resolve().parent

# 操作系统检测
_IS_WINDOWS: bool = platform.system().lower() == "windows"

# 输出目录（剪辑完成后的视频/图片保存位置）
OUTPUT_DIR: Path = Path(
    os.environ.get(
        "OUTPUT_DIR",
        str(PROJECT_ROOT / "output"),
    )
)

# 临时目录（剪辑过程中的缓存/中间文件）
TEMP_DIR: Path = Path(
    os.environ.get(
        "TEMP_DIR",
        str(PROJECT_ROOT / "temp"),
    )
)

# 项目目录（默认按平台适配）
if _IS_WINDOWS:
    _DEFAULT_PROJECT_DIR: Path = Path(
        os.environ.get("USERPROFILE", str(Path.home()))
    ) / "Videos" / "VideoEditorProjects"
else:
    _DEFAULT_PROJECT_DIR: Path = Path.home() / "Videos" / "VideoEditorProjects"

PROJECT_DIR: Path = Path(
    os.environ.get(
        "PROJECT_DIR",
        str(_DEFAULT_PROJECT_DIR),
    )
)

# 日志文件路径
LOG_DIR: Path = PROJECT_ROOT / "logs"
LOG_FILE: Path = LOG_DIR / "editor.log"

# 缓存目录（AI 推理缓存、模型下载等）
CACHE_DIR: Path = PROJECT_ROOT / ".cache"

# ─── 视频默认参数 ──────────────────────────────────────────────────────────

# 默认输出宽度（像素）
DEFAULT_WIDTH: int = _parse_int_env("DEFAULT_WIDTH", 1920)

# 默认输出高度（像素）
DEFAULT_HEIGHT: int = _parse_int_env("DEFAULT_HEIGHT", 1080)

# 默认帧率
DEFAULT_FPS: int = _parse_int_env("DEFAULT_FPS", 30)

# 默认视频格式（用于导出）
DEFAULT_VIDEO_FORMAT: str = os.environ.get("DEFAULT_VIDEO_FORMAT", "mp4")

# 默认图片格式（封面/缩略图）
DEFAULT_IMAGE_FORMAT: str = os.environ.get("DEFAULT_IMAGE_FORMAT", "png")

# 最大同时处理的 AI 任务数
MAX_CONCURRENT_AITASKS: int = _parse_int_env("MAX_CONCURRENT_AITASKS", 4)

# ─── 日志配置 ──────────────────────────────────────────────────────────────

# 默认日志级别（安全解析：无效级别名回退到 INFO）
_LOG_LEVEL_STR: str = os.environ.get("LOG_LEVEL", "INFO").upper()
DEFAULT_LOG_LEVEL: int = getattr(logging, _LOG_LEVEL_STR, logging.INFO)

# 是否启用缓存（true/false/1/0，不区分大小写）
_ENABLE_CACHE_RAW: str = os.environ.get("ENABLE_CACHE", "true").lower()
ENABLE_CACHE: bool = _ENABLE_CACHE_RAW in ("true", "1", "yes")
del _ENABLE_CACHE_RAW

# ---------------------------------------------------------------------------
# 路径初始化与校验
# ---------------------------------------------------------------------------


def _ensure_dir(d: Path, desc: str = "") -> Path:
    """
    确保目录存在，若不存在则创建。

    Parameters
    ----------
    d : Path
        需要确保的目录路径。
    desc : str, optional
        目录用途描述，用于日志提示。

    Returns
    -------
    Path
        已确保存在的目录路径。

    Raises
    ------
    OSError
        目录创建失败时抛出。
    """
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OSError(f"Failed to create {desc} directory: {d}") from exc
    return d


def ensure_directories() -> dict[str, Path]:
    """
    确保所有配置目录存在，并返回路径字典。

    Returns
    -------
    dict[str, Path]
        包含各目录路径的字典。
    """
    dirs: dict[str, Path] = {}
    for name, path, desc in [
        ("output_dir", OUTPUT_DIR, "output"),
        ("temp_dir", TEMP_DIR, "temp"),
        ("log_dir", LOG_DIR, "log"),
        ("cache_dir", CACHE_DIR, "cache"),
    ]:
        _ensure_dir(path, desc)
        dirs[name] = path
    dirs["project_dir"] = PROJECT_DIR
    dirs["log_file"] = LOG_FILE
    return dirs


# ─── 平台信息 ──────────────────────────────────────────────────────────────


def get_platform_info() -> dict:
    """
    获取当前运行平台信息。

    Returns
    -------
    dict
        包含 platform, is_windows, python_version 的平台信息字典。
    """
    return {
        "platform": platform.system(),
        "is_windows": _IS_WINDOWS,
        "python_version": platform.python_version(),
        "architecture": platform.machine(),
    }


# ---------------------------------------------------------------------------
# 日志初始化
# ---------------------------------------------------------------------------


def setup_logging() -> None:
    """初始化日志系统，配置文件和控制台输出。"""
    _ensure_dir(LOG_DIR, "log")
    logging.basicConfig(
        level=DEFAULT_LOG_LEVEL,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(str(LOG_FILE), encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


# ---------------------------------------------------------------------------
# 运行时配置更新（支持程序化修改）
# ---------------------------------------------------------------------------


def update_config(
    ollama_url: Optional[str] = None,
    ollama_model: Optional[str] = None,
    output_dir: Optional[Path] = None,
    temp_dir: Optional[Path] = None,
    default_width: Optional[int] = None,
    default_height: Optional[int] = None,
    default_fps: Optional[int] = None,
) -> dict:
    """
    动态更新配置值。此函数会修改模块级全局变量。

    Parameters
    ----------
    ollama_url : str, optional
        新的 Ollama API 地址。
    ollama_model : str, optional
        新的 Ollama 模型名称。
    output_dir : Path, optional
        新的输出目录。
    temp_dir : Path, optional
        新的临时目录。
    default_width : int, optional
        新的默认输出宽度。
    default_height : int, optional
        新的默认输出高度。
    default_fps : int, optional
        新的默认帧率。

    Returns
    -------
    dict
        本次更新的配置键值对。
    """
    _module_globals = globals()

    updates: list[tuple[str, object]] = []
    if ollama_url is not None:
        updates.append(("OLLAMA_API_URL", ollama_url))
    if ollama_model is not None:
        updates.append(("OLLAMA_MODEL_NAME", ollama_model))
    if output_dir is not None:
        updates.append(("OUTPUT_DIR", output_dir))
    if temp_dir is not None:
        updates.append(("TEMP_DIR", temp_dir))
    if default_width is not None:
        updates.append(("DEFAULT_WIDTH", default_width))
    if default_height is not None:
        updates.append(("DEFAULT_HEIGHT", default_height))
    if default_fps is not None:
        updates.append(("DEFAULT_FPS", default_fps))

    updated: dict[str, object] = {}
    for key, value in updates:
        _module_globals[key] = value
        updated[key] = value

    return updated


# ---------------------------------------------------------------------------
# 模块级信息
# ---------------------------------------------------------------------------

__all__: list[str] = [
    # API
    "OLLAMA_API_URL",
    "OLLAMA_MODEL_NAME",
    # 路径
    "PROJECT_ROOT",
    "OUTPUT_DIR",
    "TEMP_DIR",
    "PROJECT_DIR",
    "LOG_DIR",
    "LOG_FILE",
    "CACHE_DIR",
    # 视频参数
    "DEFAULT_WIDTH",
    "DEFAULT_HEIGHT",
    "DEFAULT_FPS",
    "DEFAULT_VIDEO_FORMAT",
    "DEFAULT_IMAGE_FORMAT",
    "MAX_CONCURRENT_AITASKS",
    # 配置
    "DEFAULT_LOG_LEVEL",
    "ENABLE_CACHE",
    # 辅助函数
    "ensure_directories",
    "get_platform_info",
    "update_config",
    "setup_logging",
]

# 模块加载时确保目录存在（失败不阻断 import）
try:
    ensure_directories()
except OSError:
    import warnings
    warnings.warn(
        "Failed to create config directories at import time",
        RuntimeWarning,
    )
