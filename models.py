"""
AI 视频剪辑桌面软件 - 数据模型模块

提供片段 (Segment)、项目 (Project) 和片段列表 (SegmentList) 等核心数据模型，
支持 JSON 序列化、时间码导出、片段合并等常用操作。
不依赖 Qt，可直接作为独立模块导入使用。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any


# ---------------------------------------------------------------------------
# 片段数据模型
# ---------------------------------------------------------------------------

@dataclass
class Segment:
    """视频片段数据类，表示一段具有开始/结束时间和保留状态的区间。

    Attributes:
        start_time: 起始时间（秒）。
        end_time:   结束时间（秒），必须严格大于 start_time。
        keep:       是否保留该片段（True = 保留，False = 删除）。
        label:      标签说明，如 "精彩片段"、"静音段"。
        confidence: AI 分析置信度，默认 0.0。
    """

    start_time: float
    end_time: float
    keep: bool
    label: str
    confidence: float = 0.0

    def __post_init__(self) -> None:
        """校验 end_time 必须大于 start_time，否则抛出 ValueError。"""
        if self.end_time <= self.start_time:
            raise ValueError(
                f"end_time ({self.end_time}) 必须大于 start_time ({self.start_time})"
            )

    def duration(self) -> float:
        """返回片段的时长（秒）。"""
        return self.end_time - self.start_time

    def to_dict(self) -> dict[str, Any]:
        """将片段序列化为字典，便于 JSON 序列化。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Segment:
        """从字典反序列化 Segment，支持缺失字段使用默认值。"""
        return cls(**data)

    def __repr__(self) -> str:
        return (
            f"Segment(start={self.start_time:.2f}s, end={self.end_time:.2f}s, "
            f"keep={self.keep}, label={self.label!r}, conf={self.confidence})"
        )


# ---------------------------------------------------------------------------
# 项目数据模型
# ---------------------------------------------------------------------------

@dataclass
class Project:
    """视频剪辑项目数据类，包含视频元信息和片段列表。

    Attributes:
        video_path: 视频文件路径。
        segments:   片段列表。
        transcript: 逐字稿句子列表（文本剪辑的源数据）。
        fps:        视频帧率。
        width:      视频宽度（像素）。
        height:     视频高度（像素）。
        duration:   总时长（秒）。
    """

    video_path: str
    segments: list[Segment] = field(default_factory=list)
    transcript: list[dict[str, Any]] = field(default_factory=list)
    fps: float = 30.0
    width: int = 1920
    height: int = 1080
    duration: float = 0.0
    owner: str = ""
    original_filename: str = ""
    polish: dict[str, Any] = field(default_factory=dict)

    def kept_segments(self) -> list[Segment]:
        """返回所有 keep=True 的片段列表。"""
        return [seg for seg in self.segments if seg.keep]

    def export_segments_timecode(self) -> str:
        """导出可读的时间码字符串，每行一个片段。

        Returns:
            格式如:
                [00:00:01.000 -> 00:00:03.500] 精彩片段 (keep=True, conf=0.95)
        """

        def _format_time(seconds: float) -> str:
            """将秒数格式化为 HH:MM:SS.mmm。"""
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            secs = seconds % 60
            return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"

        lines: list[str] = []
        for seg in self.segments:
            start_tc = _format_time(seg.start_time)
            end_tc = _format_time(seg.end_time)
            status = "keep" if seg.keep else "drop"
            lines.append(
                f"[{start_tc} -> {end_tc}] {seg.label} ({status}, conf={seg.confidence:.2f})"
            )
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """将项目序列化为字典，便于 JSON 序列化。"""
        return {
            "video_path": self.video_path,
            "segments": [seg.to_dict() for seg in self.segments],
            "transcript": self.transcript,
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
            "duration": self.duration,
            "owner": self.owner,
            "original_filename": self.original_filename,
            "polish": self.polish or {},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Project:
        """从字典反序列化 Project。

        Args:
            data: 包含 video_path、segments（字典列表）等字段的字典。

        Returns:
            反序列化后的 Project 实例。

        Raises:
            ValueError: 当 segments 中包含无效的片段数据时抛出。
        """
        segments = [Segment.from_dict(s) for s in data.get("segments", [])]
        return cls(
            video_path=data["video_path"],
            segments=segments,
            transcript=data.get("transcript", []),
            fps=data.get("fps", 30.0),
            width=data.get("width", 1920),
            height=data.get("height", 1080),
            duration=data.get("duration", 0.0),
            owner=data.get("owner", ""),
            original_filename=data.get("original_filename", ""),
            polish=data.get("polish") or {},
        )

    def to_json(self, indent: int = 2) -> str:
        """将项目序列化为 JSON 字符串。"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_json(cls, json_str: str) -> Project:
        """从 JSON 字符串反序列化 Project。

        Args:
            json_str: JSON 格式的字符串。

        Returns:
            反序列化后的 Project 实例。
        """
        data = json.loads(json_str)
        return cls.from_dict(data)

    def __repr__(self) -> str:
        return (
            f"Project(video={self.video_path!r}, segments={len(self.segments)}, "
            f"fps={self.fps}, size={self.width}x{self.height}, "
            f"duration={self.duration:.2f}s)"
        )


# ---------------------------------------------------------------------------
# 片段列表类
# ---------------------------------------------------------------------------

class SegmentList(list[Segment]):
    """继承 list[Segment] 的片段列表，提供片段合并、重叠检测等实用方法。

    示例:
        >>> segs = SegmentList([
        ...     Segment(0.0, 5.0, True, "片头"),
        ...     Segment(4.5, 9.0, True, "衔接"),
        ...     Segment(10.0, 15.0, False, "静音段"),
        ... ])
        >>> segs.overlaps()   # True — 前两个片段重叠
        >>> segs.sort_by_time()
        >>> segs.merge_adjacent()
    """

    def overlaps(self) -> bool:
        """检查片段列表中是否存在重叠片段。

        Returns:
            如果存在至少一对重叠片段则返回 True，否则返回 False。
            重叠定义为：两个片段的时间区间有交集（不含端点接触）。
        """
        if len(self) < 2:
            return False
        sorted_segs = sorted(self, key=lambda s: s.start_time)
        for i in range(len(sorted_segs) - 1):
            current_end = sorted_segs[i].end_time
            next_start = sorted_segs[i + 1].start_time
            # 下一个片段的开始时间小于当前片段的结束时间，视为重叠
            if next_start < current_end:
                return True
        return False

    def sort_by_time(self) -> None:
        """按 start_time 从小到大排序（原地排序）。

        当 start_time 相同时，按 end_time 从小到大排序。
        """
        super().sort(key=lambda s: (s.start_time, s.end_time))

    def merge_adjacent(self) -> None:
        """合并相邻的同状态片段。

        合并规则：
        - 两个相邻片段的 start_time 相同（即一个在前一个之后，无间隙或间隙很小）；
        - gap < 0.5 秒；
        - keep 状态相同（都是 True 或都是 False）；
        - label 拼接（中间加 " + "）。

        合并后片段:
        - start_time = 前一个片段的 start_time；
        - end_time = 后一个片段的 end_time；
        - keep = 相同的 keep 状态；
        - label = 前一个.label + " + " + 后一个.label；
        - confidence = 加权平均值。
        """
        if len(self) < 2:
            return

        self.sort_by_time()

        merged: list[Segment] = []
        i = 0
        while i < len(self):
            current = self[i]
            j = i + 1
            while j < len(self):
                next_seg = self[j]
                gap = next_seg.start_time - current.end_time
                # gap 在 [-0.5, 0.5] 范围内视为相邻
                # 使用 abs(gap) < 0.5 避免合并重叠段（gap为负时不应该合并）
                if abs(gap) < 0.5 and current.keep == next_seg.keep:
                    # 合并：延长当前片段的 end_time
                    # ⚠️ 必须先保存合并前的旧时长，再修改 end_time
                    old_dur = current.duration()
                    next_dur = next_seg.duration()
                    current.end_time = next_seg.end_time
                    current.label = f"{current.label} + {next_seg.label}"
                    # 置信度加权平均（使用合并前的旧时长）
                    current.confidence = (
                        (current.confidence * old_dur + next_seg.confidence * next_dur)
                        / (old_dur + next_dur)
                    )
                    j += 1
                else:
                    break
            merged.append(current)
            i = j

        # 替换内部列表
        self.clear()
        self.extend(merged)

    def total_keep_duration(self) -> float:
        """计算所有 keep=True 片段的总时长（秒）。

        Returns:
            所有保留片段的 duration() 之和。
        """
        return sum(seg.duration() for seg in self if seg.keep)

    def __repr__(self) -> str:
        return f"SegmentList([{len(self)} segments])"
