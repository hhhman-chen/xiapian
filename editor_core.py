"""
AI 视频剪辑桌面软件 - 视频编辑核心模块

提供视频加载、帧预览、片段裁剪和视频导出的核心编辑功能。
基于 OpenCV 和 moviepy，不依赖 Qt，可直接作为独立模块导入使用。

核心功能:
    - load_video:  加载视频并初始化为 Project
    - preview_frame: 在指定时间点抓取预览帧
    - cut_segments: 裁剪移除指定片段
    - export_video: 导出最终剪辑视频

示例用法:
    >>> project = load_video("/path/to/video.mp4")
    >>> frame = preview_frame(project, time_sec=5.0)
    >>> cut_segments(project, segments_to_remove=[seg1, seg2])
    >>> output_path = export_video(project, output_path="/path/to/output.mp4")
"""

from __future__ import annotations

import os
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from moviepy import VideoFileClip, concatenate_videoclips

import config
import models

# 模块级日志记录器
logger: logging.Logger = logging.getLogger(__name__)


# ─── 工具函数 ─────────────────────────────────────────────────────────


def _ensure_output_path(video_path: str, output_path: Optional[str] = None) -> str:
    """
    确保输出路径有效，若未指定则基于原文件名自动生成。

    Parameters
    ----------
    video_path : str
        原始视频文件路径。
    output_path : str, optional
        用户指定的输出路径。

    Returns
    -------
    str
        最终的输出文件路径（含 .mp4 扩展名）。
    """
    if output_path is not None:
        if Path(output_path).suffix == "":
            output_path = f"{output_path}.mp4"
        return output_path

    video_file = Path(video_path)
    base_name = video_file.stem
    timestamp: str = datetime.now().strftime("%Y%m%d_%H%M%S")
    new_name = f"{base_name}_edited_{timestamp}"
    return str(config.OUTPUT_DIR / f"{new_name}.mp4")


def _get_segment_clips(project: models.Project) -> tuple[list[VideoFileClip], Optional[VideoFileClip]]:
    """
    根据项目中的片段信息，提取每个 keep=True 片段的 VideoFileClip。

    只打开一次原始视频文件（共享一个源），通过 subclip 截取各片段，
    避免重复创建 FFmpeg 进程。

    Parameters
    ----------
    project : Project
        包含片段列表的剪辑项目。

    Returns
    -------
    list[VideoFileClip]
        所有保留片段的 VideoFileClip 列表，按时间顺序排列。

    Raises
    ------
    ValueError
        如果没有片段需要保留时抛出。
    """
    kept_segs: list[models.Segment] = project.kept_segments()

    if not kept_segs:
        raise ValueError("没有需要保留的片段")

    clips: list[VideoFileClip] = []
    source: Optional[VideoFileClip] = None
    try:
        # 只打开一次原始视频，所有片段共享同一个源
        source = VideoFileClip(project.video_path)
        for seg in kept_segs:
            clip = source.subclipped(start_time=seg.start_time, end_time=seg.end_time)
            clips.append(clip)
        return clips, source  # source 由调用方管理生命周期
    except Exception:
        # 异常时释放已加载的 clip，防止资源泄漏
        for c in clips:
            try:
                c.close()
            except Exception:
                pass
        if source is not None:
            try:
                source.close()
            except Exception:
                pass
        raise


# ─── 核心函数 ─────────────────────────────────────────────────────────


def load_video(path: str) -> models.Project:
    """
    加载视频文件，获取元数据并初始化为 Project 对象。

    Parameters
    ----------
    path : str
        视频文件的绝对或相对路径。

    Returns
    -------
    Project
        包含视频元数据和初始片段的 Project 对象。

    Raises
    ------
    FileNotFoundError
        当视频文件不存在时抛出。
    cv2.error
        当 OpenCV 无法读取视频文件时抛出。
    ValueError
        当视频信息异常（如 fps=0）时抛出。
    """
    cap: Optional[cv2.VideoCapture] = None
    try:
        if not os.path.isfile(path):
            raise FileNotFoundError(f"视频文件不存在: {path}")

        cap = cv2.VideoCapture(path)

        if not cap.isOpened():
            raise cv2.error("无法打开视频文件，可能格式不支持或文件已损坏")

        # 获取视频元数据
        fps: float = cap.get(cv2.CAP_PROP_FPS)
        width: int = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height: int = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        frame_count_raw: float = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        # OpenCV 的 CAP_PROP_FRAME_COUNT 可能返回浮点数，取整
        frame_count: int = int(frame_count_raw)
        total_duration: float = frame_count / fps if fps > 0 else 0.0

        if fps <= 0:
            raise ValueError(f"视频 fps 异常: {fps}")
        if width <= 0 or height <= 0:
            raise ValueError(f"视频分辨率异常: {width}x{height}")

        # 更新全局配置（可选）
        config.DEFAULT_FPS = int(fps)
        config.DEFAULT_WIDTH = width
        config.DEFAULT_HEIGHT = height

        # 创建初始 Segment：整个视频作为一段
        initial_segment: models.Segment = models.Segment(
            start_time=0.0,
            end_time=total_duration,
            keep=True,
            label="全片",
            confidence=1.0,
        )

        project: models.Project = models.Project(
            video_path=path,
            segments=[initial_segment],
            fps=fps,
            width=width,
            height=height,
            duration=total_duration,
        )

        logger.info(
            "视频加载成功: path=%s, fps=%.1f, size=%dx%d, duration=%.2fs",
            path, fps, width, height, total_duration,
        )
        return project

    except FileNotFoundError:
        raise
    except Exception as exc:
        logger.error("加载视频失败: path=%s, error=%s", path, exc)
        raise
    finally:
        if cap is not None and cap.isOpened():
            cap.release()


def preview_frame(project: models.Project, time_sec: float) -> np.ndarray:
    """
    在指定时间点从视频中抓取一帧作为预览。

    Parameters
    ----------
    project : Project
        视频剪辑项目，包含视频路径和元数据。
    time_sec : float
        预览时间点（秒），从视频开头算起。

    Returns
    -------
    np.ndarray
        BGR 格式的 numpy 数组（uint8），形状为 (height, width, 3)。

    Raises
    ------
    ValueError
        当时间点超出视频范围时抛出。
    """
    cap: Optional[cv2.VideoCapture] = None
    try:
        # 钳制时间范围
        time_sec = max(0.0, min(time_sec, project.duration))

        cap = cv2.VideoCapture(project.video_path)

        if not cap.isOpened():
            raise cv2.error(f"无法打开视频文件: {project.video_path}")

        target_frame_idx: int = int(time_sec * project.fps)

        # 读取目标帧（最多重试 3 次，每次重试前重新 seek 同一帧）
        ret: bool = False
        frame: Optional[np.ndarray] = None
        for _ in range(3):
            if not cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame_idx):
                logger.warning(
                    "Seek 到目标帧失败: frame_idx=%d, 尝试读取当前位置",
                    target_frame_idx,
                )
            ret, frame = cap.read()
            if ret and frame is not None:
                break

        if not ret or frame is None:
            raise cv2.error("读取视频帧失败")

        logger.debug(
            "预览帧抓取成功: time=%.2fs, frame_idx=%d, shape=%s",
            time_sec, target_frame_idx, frame.shape,
        )
        return frame

    except cv2.error:
        raise
    except Exception as exc:
        logger.error("预览帧失败: time=%.2fs, error=%s", time_sec, exc)
        raise
    finally:
        if cap is not None and cap.isOpened():
            cap.release()


def cut_segments(
    project: models.Project,
    segments_to_remove: list[models.Segment],
) -> None:
    """
    从项目中移除指定片段，并更新剩下的 keep=True 片段。

    使用值比较（start_time + end_time）而非对象身份比较，
    避免浮点精度问题。

    Parameters
    ----------
    project : Project
        需要裁剪的视频剪辑项目。
    segments_to_remove : list[Segment]
        需要移除的片段列表。

    Raises
    ------
    ValueError
        当所有片段都被移除时抛出。
    """
    try:
        if not segments_to_remove:
            logger.info("没有需要移除的片段，跳过裁剪")
            return

        # 用值比较替代 id() 比较
        # 比较 (start_time, end_time) 元组，浮点比较用 round 到 3 位小数
        remove_keys: set[tuple[float, float]] = {
            (round(s.start_time, 3), round(s.end_time, 3)) for s in segments_to_remove
        }

        removed_info: str = ", ".join(
            f"[{s.start_time:.1f}s-{s.end_time:.1f}s]"
            for s in segments_to_remove
        )
        logger.info("开始裁剪片段: %s", removed_info)

        project.segments = [
            seg for seg in project.segments 
            if (round(seg.start_time, 3), round(seg.end_time, 3)) not in remove_keys
        ]

        # 将剩下的片段全部标记为保留
        for seg in project.segments:
            seg.keep = True

        if not project.segments:
            raise ValueError("所有片段都被移除了，视频变为空")

        # 重新计算总时长
        project.duration = sum(s.duration() for s in project.segments)

        logger.info(
            "裁剪完成: 剩余 %d 个片段, 总时长=%.2fs",
            len(project.segments), project.duration,
        )

    except ValueError:
        raise
    except Exception as exc:
        logger.error("裁剪片段失败: error=%s", exc)
        raise


def export_video(
    project: models.Project,
    output_path: Optional[str] = None,
) -> str:
    """
    使用 moviepy 导出视频，只保留 keep=True 的片段并拼接。

    Parameters
    ----------
    project : Project
        包含片段列表的剪辑项目。
    output_path : str, optional
        输出文件路径。若为 None，则自动生成基于原文件名的路径。

    Returns
    -------
    str
        最终输出的视频文件路径。

    Raises
    ------
    FileNotFoundError
        当原视频文件不存在时抛出。
    """
    clips: list[VideoFileClip] = []
    source: Optional[VideoFileClip] = None
    final_clip: Optional[VideoFileClip] = None
    try:
        final_output_path: str = _ensure_output_path(
            video_path=project.video_path,
            output_path=output_path,
        )

        if not os.path.isfile(project.video_path):
            raise FileNotFoundError(f"原视频文件不存在: {project.video_path}")

        kept_segs: list[models.Segment] = project.kept_segments()

        if not kept_segs:
            raise ValueError("没有需要保留的片段，无法导出")

        kept_segs.sort(key=lambda s: s.start_time)

        logger.info(
            "开始导出视频: %d 个片段, output=%s",
            len(kept_segs), final_output_path,
        )

        # 提取所有保留片段的 clip（元组：clips, source）
        clips, source = _get_segment_clips(project)

        if not clips:
            raise ValueError("提取片段失败，clips 列表为空")

        # 拼接所有片段
        final_clip = concatenate_videoclips(clips, method="compose")

        # 写入输出文件（MoviePy 2.x 不传 temp_audiofile，让库自动处理）
        final_clip.write_videofile(
            filename=final_output_path,
            fps=project.fps,
            codec="libx264",
            audio_codec="aac",
            remove_temp=True,
        )

        logger.info(
            "视频导出成功: output=%s, duration=%.2fs, fps=%.1f",
            final_output_path, project.duration, project.fps,
        )
        return final_output_path

    except FileNotFoundError:
        raise
    except Exception as exc:
        logger.error("导出视频失败: error=%s", exc)
        raise
    finally:
        # 确保所有资源释放
        if final_clip is not None:
            try:
                final_clip.close()
            except Exception:
                pass
        for clip in clips:
            try:
                clip.close()
            except Exception:
                pass
        if source is not None:
            try:
                source.close()
            except Exception:
                pass
