"""
AI 视频分析模块
==================

调用 DGX Spark Ollama API 自动分析视频内容，识别精彩片段、静音段和过场段。

功能概述:
    - 从视频中均匀采样 10 个关键帧
    - 将帧编码为 base64 JPEG
    - 调用 Ollama API 进行 AI 分析
    - 返回 AI 推荐的 segments 列表

使用示例:
    >>> from ai_analyzer import analyze_video
    >>> from editor_core import load_video
    >>> project = load_video("video.mp4")
    >>> segments = analyze_video(project)
    >>> for seg in segments:
    ...     print(f"{seg.start_time}s - {seg.end_time}s: {seg.label} (keep={seg.keep})")

环境变量:
    MOCK_AI=true   使用 mock 模式，返回预设测试片段（默认启用）
    MOCK_AI=false  真正调用 Ollama API
    MOCK_AI=1      同 true
    MOCK_AI=0      同 false

Author: AI Video Editor Team
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import time
from typing import Any, Optional

import cv2
import numpy as np
import requests

import config
import models

# 模块级日志记录器
logger: logging.Logger = logging.getLogger(__name__)

# ─── 模块级常量 ─────────────────────────────────────────────────────────

# 是否使用 mock 模式（通过环境变量 MOCK_AI 控制）
# 支持的值: "true"/"True"/"1" → 启用 mock, "false"/"False"/"0" → 禁用 mock
USE_MOCK: bool = os.environ.get("MOCK_AI", "false").lower() in ("true", "1")

# Ollama API 超时时间（秒）
OLLAMA_TIMEOUT: int = 60

# ─── 系统 Prompt 模板 ───────────────────────────────────────────────────


_SYSTEM_PROMPT: str = (
    "你是一个专业的视频内容分析助手。"
    "你的任务：分析以下 {num_frames} 个等距采样的视频关键帧，"
    "识别哪些时间段是精彩片段（演讲/动作/重点内容），"
    "哪些是静音/空白/转场段（建议删除）。"
    "\n\n"
    "视频总时长：{duration}s，总帧数：{num_frames} 帧。"
    "每帧大约间隔 {frame_interval}s。\n\n"
    "请严格按以下 JSON 格式返回分析结果，不要添加任何其他文字：\n"
    '[\n'
    '  {"start_time": 0.0, "end_time": 5.0, "keep": true, "label": "精彩片段", "confidence": 0.95},\n'
    '  {"start_time": 5.0, "end_time": 8.0, "keep": false, "label": "静音段", "confidence": 0.80}\n'
    ']\n\n'
    "要求：\n"
    "1. 每段必须覆盖完整视频时间范围（从 0 到总时长），不能有重叠和遗漏\n"
    "2. keep 为 true 表示保留，false 表示建议删除\n"
    "3. label 使用中文：\"精彩片段\"、\"静音段\"、\"转场段\"、\"演讲\"、\"动作\" 等\n"
    "4. confidence 范围 0.0~1.0，表示 AI 判断的置信度\n"
    "5. 请根据关键帧内容合理判断每一段的 keep 状态"
)


# ─── Mock 预设测试数据 ───────────────────────────────────────────────────

# 当 USE_MOCK=True 时返回的 5 个预设测试片段
# 适用于典型 30 秒演讲视频的分割结果
_MOCK_SEGMENTS: list[dict[str, Any]] = [
    {"start_time": 0.0, "end_time": 4.0, "keep": True, "label": "片头精彩", "confidence": 0.92},
    {"start_time": 4.0, "end_time": 8.0, "keep": False, "label": "静音段", "confidence": 0.85},
    {"start_time": 8.0, "end_time": 18.0, "keep": True, "label": "演讲段落", "confidence": 0.95},
    {"start_time": 18.0, "end_time": 22.0, "keep": False, "label": "转场段", "confidence": 0.78},
    {"start_time": 22.0, "end_time": 30.0, "keep": True, "label": "高潮部分", "confidence": 0.90},
]


# ─── 工具函数 ───────────────────────────────────────────────────


def _sample_frames(
    video_path: str,
    num_samples: int = 10,
) -> list[np.ndarray]:
    """
    从视频中均匀采样关键帧。

    在视频时间线上均匀选取 num_samples 个时间点，
    逐帧读取并返回对应的 numpy 数组。

    Parameters
    ----------
    video_path : str
        视频文件路径。
    num_samples : int
        采样帧数，默认 10。

    Returns
    -------
    list[np.ndarray]
        采样到的帧列表，每个元素是 BGR 格式的 numpy uint8 数组，
        形状为 (height, width, 3)。

    Raises
    ------
    cv2.error
        当无法打开视频文件时抛出。
    """
    cap: Optional[cv2.VideoCapture] = None
    frames: list[np.ndarray] = []
    try:
        if not os.path.isfile(video_path):
            raise FileNotFoundError(f"视频文件不存在: {video_path}")

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise cv2.error(f"无法打开视频文件: {video_path}")

        total_frames: int = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            # 如果无法获取帧数，则使用 fps * 估算时长
            fps: float = cap.get(cv2.CAP_PROP_FPS)
            duration: float = cap.get(cv2.CAP_PROP_DURATION) / 1000.0
            total_frames = max(int(duration * fps), 1)

        if total_frames <= num_samples:
            # 帧数少于采样数，全部读取
            sample_indices: list[int] = list(range(total_frames))
        else:
            # 均匀间隔采样
            step: float = (total_frames - 1) / (num_samples - 1)
            sample_indices = [int(i * step) for i in range(num_samples)]

        for idx in sample_indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret: bool = False
            frame: Optional[np.ndarray] = None
            for _ in range(3):
                ret, frame = cap.read()
                if ret and frame is not None:
                    break
            if ret and frame is not None:
                frames.append(frame)
            else:
                logger.warning("采样帧读取失败，使用黑帧替代: idx=%d", idx)
                # 用黑帧替代读取失败的帧
                h, w = frame.shape[:2] if frame is not None else (1080, 1920)
                frames.append(np.zeros((h, w, 3), dtype=np.uint8))

        logger.info(
            "帧采样完成: path=%s, sampled=%d, total_frames=%d",
            video_path, len(frames), total_frames,
        )
        return frames

    except FileNotFoundError:
        raise
    except Exception as exc:
        logger.error("帧采样失败: path=%s, error=%s", video_path, exc)
        raise
    finally:
        if cap is not None and cap.isOpened():
            cap.release()


def _encode_frame(frame: np.ndarray) -> str:
    """
    将 numpy frame 数组编码为 base64 格式的 JPEG 字符串。

    使用 OpenCV 将 BGR numpy 数组编码为 JPEG 内存字节流，
    再转为 base64 字符串，适合通过 HTTP API 传输。

    Parameters
    ----------
    frame : np.ndarray
        BGR 格式的 numpy uint8 数组，形状为 (height, width, 3)。

    Returns
    -------
    str
        base64 编码的 JPEG 字符串（不含 data URI 前缀）。
    """
    try:
        # cv2.imencode 将 numpy 数组编码为内存字节流
        # 返回 (成功标志, 编码后的字节数组)
        result_tuple: tuple[bool, bytes] = cv2.imencode(".jpg", frame)
        success: bool = result_tuple[0]
        encoded: bytes = result_tuple[1]
        if not success:
            raise cv2.error("JPEG 编码失败")

        # 转为 base64 字符串
        b64_str: str = base64.b64encode(encoded).decode("utf-8")
        return b64_str

    except cv2.error:
        raise
    except Exception as exc:
        logger.error("帧编码失败: error=%s", exc)
        raise


def _parse_ai_response(text: str) -> list[dict[str, Any]]:
    """
    解析 AI 返回的 JSON 文本，提取 segments 数据。

    自动提取 JSON 数组部分（处理 AI 可能在 JSON 前后添加文字的情况），
    并校验每段数据的字段完整性。

    Parameters
    ----------
    text : str
        AI 返回的原始文本，包含 JSON 格式的 segments 列表。

    Returns
    -------
    list[dict]
        解析后的 segments 字典列表，每个字典包含:
        start_time (float), end_time (float), keep (bool),
        label (str), confidence (float)。

    Raises
    ------
    ValueError
        当 JSON 格式无效或字段缺失时抛出。
    """
    try:
        # 尝试直接解析（如果整个文本就是 JSON）
        try:
            data: list[dict[str, Any]] = json.loads(text)
        except json.JSONDecodeError:
            # 直接解析失败（文本可能包含前后文字），提取 JSON 数组
            start: int = text.find('[')
            end: int = text.rfind(']')
            if start != -1 and end != -1 and end > start:
                json_str: str = text[start:end + 1]
                data = json.loads(json_str)
            else:
                raise ValueError(f"无法从 AI 响应中提取 JSON 数组: {text[:200]}")

        if not isinstance(data, list):
            raise ValueError(f"AI 响应不是列表格式: type={type(data).__name__}")

        # 校验每个 segment 的必需字段
        required_keys: set[str] = {"start_time", "end_time", "keep", "label", "confidence"}
        parsed_segments: list[dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict):
                logger.warning("跳过非 dict 元素: %s", item)
                continue
            # 检查必需字段
            missing: set[str] = required_keys - set(item.keys())
            if missing:
                logger.warning("segment 缺少字段 %s，使用默认值", missing)
            # 确保字段类型正确
            parsed_segments.append({
                "start_time": float(item.get("start_time", 0.0)),
                "end_time": float(item.get("end_time", 0.0)),
                "keep": bool(item.get("keep", True)),
                "label": str(item.get("label", "未知")),
                "confidence": float(item.get("confidence", 0.5)),
            })

        logger.info("AI 响应解析成功: 共 %d 个 segments", len(parsed_segments))
        return parsed_segments

    except json.JSONDecodeError as exc:
        logger.error("AI 响应 JSON 解析失败: %s", exc)
        raise ValueError(f"AI 响应 JSON 格式错误: {exc}") from exc


def _build_user_prompt(
    frames_b64: list[str],
    duration: float,
    num_frames: int,
) -> str:
    """
    构建 Ollama API 请求中的 user content，包含关键帧的 base64 数据。

    Parameters
    ----------
    frames_b64 : list[str]
        每帧的 base64 编码 JPEG 字符串。
    duration : float
        视频总时长（秒）。
    num_frames : int
        采样帧数。

    Returns
    -------
    str
        包含帧数据和分析提示的 user content 字符串。
    """
    frame_interval: float = duration / (num_frames - 1) if num_frames > 1 else 0.0

    # 构建帧索引说明
    frame_indices_text: str = "\n".join(
        f"帧 {i + 1}/{num_frames} (时间: {i * frame_interval:.1f}s) "
        f"→ base64 数据见附件"
        for i in range(num_frames)
    )

    user_content: str = (
        f"以下是 {num_frames} 个等距采样的视频关键帧的 base64 编码 JPEG 数据。\n\n"
        f"视频总时长: {duration}s\n"
        f"帧间隔: 约 {frame_interval:.1f}s\n\n"
        f"帧索引:\n{frame_indices_text}\n\n"
        "请分析这些帧的内容，判断每一帧之间对应的时间段是否值得保留。\n"
        "返回 JSON 格式的 segments 列表。"
    )

    return user_content


def _call_ollama_api(
    system_prompt: str,
    user_content: str,
    frames_b64: list[str],
    model: str,
    api_url: str,
) -> str:
    """
    调用 Ollama API /api/chat 端点获取 AI 分析结果。

    Parameters
    ----------
    system_prompt : str
        系统提示词，描述分析任务和要求。
    user_content : str
        用户内容，包含帧数据和具体请求。
    frames_b64 : list[str]
        帧的 base64 数据列表。
    model : str
        Ollama 模型名称。
    api_url : str
        Ollama API 基础地址。

    Returns
    -------
    str
        AI 返回的原始文本（包含 JSON segments）。

    Raises
    ------
    requests.RequestException
        当网络请求失败时抛出。
    """
    url: str = f"{api_url.rstrip('/')}/api/chat"

    # 构建 messages 数组
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": user_content,
            "images": frames_b64,  # Ollama 的 images 字段接收 base64 JPEG
        },
    ]

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0.3},
    }

    # 发起 HTTP 请求
    response: requests.Response = requests.post(
        url,
        json=payload,
        timeout=OLLAMA_TIMEOUT,
    )
    response.raise_for_status()

    result: dict[str, Any] = response.json()

    # Ollama /api/chat 返回格式中，消息内容在 message.content 字段
    content: str = result.get("message", {}).get("content", "")

    if not content:
        raise ValueError("Ollama API 返回的消息内容为空")

    return content


def _get_default_segment(
    duration: float,
) -> list[dict[str, Any]]:
    """
    获取默认的 fallback segment：保留整个视频。

    Parameters
    ----------
    duration : float
        视频总时长。

    Returns
    -------
    list[dict]
        包含单个 segment 的列表。
    """
    return [
        {
            "start_time": 0.0,
            "end_time": duration,
            "keep": True,
            "label": "全片保留",
            "confidence": 0.5,
        }
    ]


def _mock_analyze(
    duration: float,
) -> list[dict[str, Any]]:
    """
    Mock 分析：根据视频实际时长等比缩放预设测试片段。

    Parameters
    ----------
    duration : float
        视频总时长（秒），用于等比缩放预设片段时间。

    Returns
    -------
    list[dict]
        按视频时长缩放后的测试片段。
    """
    scale = duration / 30.0 if duration > 0 else 1.0
    scaled: list[dict[str, Any]] = []
    for seg in _MOCK_SEGMENTS:
        s = seg.copy()
        s["start_time"] = round(s["start_time"] * scale, 1)
        s["end_time"] = round(s["end_time"] * scale, 1)
        scaled.append(s)
    # 对齐最后一个片段的 end_time 到视频末尾，避免浮点误差
    if scaled:
        scaled[-1]["end_time"] = round(duration, 1)
    logger.info("Mock 模式: 返回 %d 个预设片段 (视频时长=%.2fs)", len(scaled), duration)
    return scaled


# ─── 主分析函数 ───────────────────────────────────────────────────


def analyze_video(project: models.Project) -> list[models.Segment]:
    """
    分析视频内容，返回 AI 推荐的 segments 列表。

    主要流程:
    1. 如果 USE_MOCK=True，返回预设的 mock 片段；否则调用 Ollama API
    2. 从视频中均匀采样 10 个关键帧
    3. 将帧编码为 base64 JPEG
    4. 构建系统提示和用户内容，调用 Ollama API
    5. 解析 API 返回的 JSON，构建 Segment 列表

    Parameters
    ----------
    project : Project
        视频剪辑项目，包含视频路径和元数据。

    Returns
    -------
    list[Segment]
        AI 分析后的片段列表，每个 Segment 包含:
        start_time (float): 起始时间（秒）
        end_time (float): 结束时间（秒）
        keep (bool): 是否保留
        label (str): 片段标签
        confidence (float): 置信度 (0.0~1.0)

    Raises
    ------
    FileNotFoundError
        当视频文件不存在时抛出。
    requests.RequestException
        当 Ollama API 网络请求失败时抛出。
    """
    video_path: str = project.video_path
    duration: float = project.duration
    model: str = config.OLLAMA_MODEL_NAME
    api_url: str = config.OLLAMA_API_URL

    logger.info(
        "开始分析视频: path=%s, duration=%.2fs, model=%s, mock=%s",
        video_path, duration, model, USE_MOCK,
    )

    # ─── Mock 模式 ──
    if USE_MOCK:
        parsed_data: list[dict[str, Any]] = _mock_analyze(duration)
        segments: list[models.Segment] = [
            models.Segment(
                start_time=seg.get("start_time", 0.0),
                end_time=seg.get("end_time", duration),
                keep=seg.get("keep", True),
                label=seg.get("label", "未知"),
                confidence=seg.get("confidence", 0.5),
            )
            for seg in parsed_data
        ]
        logger.info("Mock 分析完成: %d segments", len(segments))
        return segments

    # ─── 真实 API 调用 ──
    try:
        # 步骤 1: 采样关键帧
        frames: list[np.ndarray] = _sample_frames(video_path, num_samples=10)

        if not frames:
            raise ValueError("未采样到任何关键帧")

        # 步骤 2: 编码为 base64 JPEG
        frames_b64: list[str] = [_encode_frame(frame) for frame in frames]

        # 步骤 3: 构建提示
        num_frames: int = len(frames)
        frame_interval: float = duration / (num_frames - 1) if num_frames > 1 else 0.0

        system_prompt: str = _SYSTEM_PROMPT.format(
            num_frames=num_frames,
            duration=duration,
            frame_interval=frame_interval,
        )
        user_content: str = _build_user_prompt(frames_b64, duration, num_frames)

        # 步骤 4: 调用 Ollama API
        start_time: float = time.time()
        ai_text: str = _call_ollama_api(
            system_prompt=system_prompt,
            user_content=user_content,
            frames_b64=frames_b64,
            model=model,
            api_url=api_url,
        )
        elapsed: float = time.time() - start_time
        logger.info("Ollama API 调用成功: %.2fs", elapsed)

        # 步骤 5: 解析 AI 返回的 JSON
        parsed_data = _parse_ai_response(ai_text)

        # 步骤 6: 构建 Segment 对象
        segments: list[models.Segment] = [
            models.Segment(
                start_time=seg.get("start_time", 0.0),
                end_time=seg.get("end_time", duration),
                keep=seg.get("keep", True),
                label=seg.get("label", "未知"),
                confidence=seg.get("confidence", 0.5),
            )
            for seg in parsed_data
        ]

        logger.info("分析完成: %d segments, duration=%.2fs", len(segments), duration)
        return segments

    except requests.exceptions.Timeout:
        logger.warning("Ollama API 调用超时 (%ds)，使用 fallback", OLLAMA_TIMEOUT)
        return _get_default_segment(duration)
    except requests.exceptions.ConnectionError as exc:
        logger.warning("Ollama API 连接失败 (%s)，使用 fallback", exc)
        return _get_default_segment(duration)
    except requests.exceptions.RequestException as exc:
        logger.warning("Ollama API 请求异常 (%s)，使用 fallback", exc)
        return _get_default_segment(duration)
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning("AI 响应解析异常 (%s)，使用 fallback", exc)
        return _get_default_segment(duration)
    except Exception as exc:
        logger.warning("分析过程未知异常 (%s)，使用 fallback", exc)
        return _get_default_segment(duration)


# ─── 模块导出 ───────────────────────────────────────────────────

__all__: list[str] = [
    # 公共函数
    "analyze_video",
    "USE_MOCK",
    # 工具函数
    "_sample_frames",
    "_encode_frame",
    "_parse_ai_response",
    # 辅助函数
    "_build_user_prompt",
    "_call_ollama_api",
    "_mock_analyze",
    "_get_default_segment",
]
