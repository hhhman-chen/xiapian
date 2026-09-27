"""
AI 视频剪辑桌面软件 - 时间线组件 (TimelineWidget)

提供基于 QPainter 的横向时间线可视化组件，支持点击、悬停、
拖拽边界调整、滚轮缩放等交互操作。

使用示例:
    >>> widget = TimelineWidget()
    >>> project = Project(video_path="video.mp4", duration=60.0, segments=[...])
    >>> widget.set_project(project)
    >>> widget.show()
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

from PyQt5.QtCore import Qt, QEvent, QPointF, pyqtSignal, QRectF, QSize
from PyQt5.QtGui import (
    QPainter,
    QPen,
    QBrush,
    QColor,
    QFont,
    QMouseEvent,
    QWheelEvent,
    QResizeEvent,
    QPaintEvent,
)
from PyQt5.QtWidgets import QWidget, QSizePolicy

from models import Segment, Project

# ===========================================================================
# 常量定义
# ===========================================================================

SEGMENT_HEIGHT: int = 48          # 片段矩形条高度
SEGMENT_SPACING: int = 6          # 片段行间距
RULER_HEIGHT: int = 28            # 时间标尺区域高度
LABEL_COLUMN_WIDTH: int = 80      # 左侧标签列宽度
MIN_SEGMENT_WIDTH: int = 12       # 最小片段宽度 (px)
ZOOM_FACTOR: float = 1.25         # 缩放因子 (每次滚轮变化倍率)
BASE_PPS: float = 50.0            # 基础像素/秒比
BORDER_HIT_WIDTH: int = 8         # 拖拽边界热区宽度
MIN_ZOOM: float = 0.3             # 最小缩放
MAX_ZOOM: float = 5.0             # 最大缩放

# 颜色常量
COLOR_BG: QColor = QColor(32, 32, 32)          # 背景
COLOR_GRID: QColor = QColor(60, 60, 60)        # 网格线
COLOR_TEXT: QColor = QColor(220, 220, 220)     # 文字色
COLOR_RULER_BG: QColor = QColor(26, 26, 26)    # 标尺背景
COLOR_HOVER: QColor = QColor(180, 180, 180, 80)  # 悬停高亮
COLOR_GREEN: QColor = QColor(76, 175, 80)      # 保留-绿
COLOR_RED: QColor = QColor(244, 67, 54)        # 删除-红
COLOR_GREEN_HL: QColor = QColor(100, 200, 100) # 保留高亮
COLOR_RED_HL: QColor = QColor(244, 120, 110)   # 删除高亮
COLOR_WHITE_90: QColor = QColor(255, 255, 255, 90)

# ===========================================================================
# 片段绘制数据缓存
# ===========================================================================


class _SegmentDrawData:
    """单个片段的绘制缓存数据。

    Attributes:
        segment: 对应的 Segment 对象。
        x: 左边缘 x 坐标 (px)。
        y: 上边缘 y 坐标 (px)。
        w: 宽度 (px)。
        h: 高度 (px)。
        hovered: 当前是否被鼠标悬停。
        left_edge: 左边界拖拽热区。
        right_edge: 右边界拖拽热区。
    """

    __slots__ = ("segment", "x", "y", "w", "h", "hovered", "left_edge", "right_edge")

    def __init__(self, segment: Segment, x: float, y: float, w: float, h: float) -> None:
        self.segment = segment
        self.x = x
        self.y = y
        self.w = w
        self.h = h
        self.hovered = False
        self.left_edge = QRectF(x, y, BORDER_HIT_WIDTH, h)
        self.right_edge = QRectF(x + w - BORDER_HIT_WIDTH, y, BORDER_HIT_WIDTH, h)

# ===========================================================================
# TimelineWidget 主类
# ===========================================================================


class TimelineWidget(QWidget):
    """视频剪辑时间线可视化组件。

    使用 QPainter 在 QWidget 上直接绘制片段矩形条、时间标尺等元素。
    支持点击切换片段状态、拖拽调整边界、滚轮缩放等功能。

    信号:
        segment_toggled(index, keep): 片段被点击切换时发射。
        segment_resized(index, new_start, new_end): 片段边界被拖拽后发射。
        time_selected(time_sec): 在时间线空白区域点击时发射。
    """

    # ---- 信号定义 ----
    segment_toggled: any = pyqtSignal(int, bool)
    segment_resized: any = pyqtSignal(int, float, float)
    time_selected: any = pyqtSignal(float)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        # ---- 状态变量 ----
        self.project: Optional[Project] = None   # 当前加载的项目
        self.zoom_level: float = 1.0             # 缩放因子 (相对于 BASE_PPS)
        self.scroll_offset: float = 0.0          # 水平滚动偏移 (秒)

        # 绘制数据缓存 (按索引顺序)
        self._draw_data: List[_SegmentDrawData] = []

        # 鼠标交互状态
        self._dragging: bool = False             # 是否正在拖拽
        self._drag_edge: str = ""                # "left" | "right" | "body"
        self._drag_index: int = -1               # 被拖拽片段的索引
        self._drag_start_px: float = 0.0         # 拖拽起始像素
        self._drag_start_time: float = 0.0       # 拖拽起始时间 (秒)

        self._hover_index: int = -1              # 鼠标悬停的片段索引
        self._hover_pos: QPointF = QPointF(0, 0) # 鼠标当前像素位置

        # ---- UI 子控件 ----
        self._duration_label: str = "0.0s"       # 总时长标签文本

        # 设置样式
        self._apply_styles()
        self._set_minimum_size()

    # -----------------------------------------------------------------------
    # 公共接口
    # -----------------------------------------------------------------------

    def set_project(self, project: Project) -> None:
        """加载 Project 到时间线。

        Args:
            project: 包含片段列表的项目对象。
        """
        try:
            self.project = project
            self._rebuild_draw_data()
            self._update_duration_label()
            self.scroll_offset = 0.0
            self.zoom_level = 1.0
            self.update()
        except Exception as exc:
            raise RuntimeError(f"加载项目到时间线失败: {project}") from exc

    def zoom_in(self) -> None:
        """放大时间线。"""
        try:
            self.zoom_level = min(self.zoom_level * ZOOM_FACTOR, MAX_ZOOM)
            self._rebuild_draw_data()
            self.update()
        except Exception as exc:
            raise RuntimeError("放大时间线失败") from exc

    def zoom_out(self) -> None:
        """缩小时间线。"""
        try:
            self.zoom_level = max(self.zoom_level / ZOOM_FACTOR, MIN_ZOOM)
            self._rebuild_draw_data()
            self.update()
        except Exception as exc:
            raise RuntimeError("缩小时间线失败") from exc

    def get_modified_segments(self) -> List[Segment]:
        """获取所有当前显示的片段（即被时间线展示的那些）。

        Returns:
            片段列表。
        """
        try:
            if self.project is None:
                return []
            return list(self.project.segments)
        except Exception as exc:
            raise RuntimeError("获取修改片段失败") from exc

    # -----------------------------------------------------------------------
    # 尺寸 & 布局
    # -----------------------------------------------------------------------

    def _set_minimum_size(self) -> None:
        """设置最小尺寸。"""
        min_w: int = int(BASE_PPS * 10) + LABEL_COLUMN_WIDTH + 40
        min_h: int = RULER_HEIGHT + SEGMENT_HEIGHT + SEGMENT_SPACING + 40
        self.setMinimumSize(min_w, min_h)

    def _content_width(self) -> float:
        """计算内容总宽度 (px) = 总时长 * pps。"""
        if self.project is None or self.project.duration <= 0:
            return BASE_PPS * 10.0
        pps: float = BASE_PPS * self.zoom_level
        return self.project.duration * pps

    def _content_height(self) -> float:
        """计算内容总高度 (px)。"""
        if self.project is None:
            return SEGMENT_HEIGHT + SEGMENT_SPACING
        return len(self.project.segments) * (SEGMENT_HEIGHT + SEGMENT_SPACING)

    # -----------------------------------------------------------------------
    # 像素 <-> 时间 换算
    # -----------------------------------------------------------------------

    def _px_to_time(self, px: float) -> float:
        """像素坐标 -> 时间 (秒)，考虑滚动偏移。"""
        pps: float = BASE_PPS * self.zoom_level
        time_sec: float = (px - self.scroll_offset * pps) / pps
        return max(0.0, time_sec)

    def _time_to_px(self, time_sec: float) -> float:
        """时间 (秒) -> 像素坐标。"""
        pps: float = BASE_PPS * self.zoom_level
        return time_sec * pps

    # -----------------------------------------------------------------------
    # 重绘绘制数据
    # -----------------------------------------------------------------------

    def _rebuild_draw_data(self) -> None:
        """根据当前 project 和缩放状态重新构建绘制数据缓存。"""
        try:
            if self.project is None or len(self.project.segments) == 0:
                self._draw_data = []
                return

            pps: float = BASE_PPS * self.zoom_level
            segments: List[Segment] = self.project.segments
            data_list: List[_SegmentDrawData] = []

            for seg in segments:
                # 计算片段矩形位置
                x: float = self._time_to_px(seg.start_time)
                w: float = max(MIN_SEGMENT_WIDTH, (seg.end_time - seg.start_time) * pps)
                y: float = 0.0

                dd: _SegmentDrawData = _SegmentDrawData(
                    segment=seg, x=x, y=y, w=w, h=float(SEGMENT_HEIGHT),
                )
                dd.left_edge = QRectF(dd.x, dd.y, BORDER_HIT_WIDTH, dd.h)
                dd.right_edge = QRectF(
                    dd.x + dd.w - BORDER_HIT_WIDTH, dd.y, BORDER_HIT_WIDTH, dd.h
                )
                data_list.append(dd)

            self._draw_data = data_list
        except Exception as exc:
            raise RuntimeError("重建绘制数据失败") from exc

    def _update_duration_label(self) -> None:
        """更新总时长标签文本。"""
        if self.project is not None:
            self._duration_label = f"{self.project.duration:.1f}s"
        else:
            self._duration_label = "0.0s"

    # -----------------------------------------------------------------------
    # 样式
    # -----------------------------------------------------------------------

    def _apply_styles(self) -> None:
        """应用 Qt 样式表。"""
        self.setStyleSheet(
            "TimelineWidget { background-color: #202020; }"
        )

    # -----------------------------------------------------------------------
    # 绘制辅助
    # -----------------------------------------------------------------------

    def _tick_interval(self) -> float:
        """根据缩放级别动态计算标尺刻度间隔 (秒)。"""
        interval: float = self._content_width() / 10
        candidates: List[float] = [0.5, 1.0, 2.0, 5.0, 10.0, 15.0, 30.0, 60.0]
        for candidate in candidates:
            if candidate <= interval and candidate * 2 > interval:
                return candidate
        return candidates[-1] if candidates else 10.0

    def _draw_ruler(self, painter: QPainter) -> None:
        """绘制顶部时间标尺。"""
        try:
            if self.project is None:
                return

            pps: float = BASE_PPS * self.zoom_level
            content_w: float = self._content_width()
            ruler_rect: QRectF = QRectF(0, 0, content_w, RULER_HEIGHT)
            painter.setClipping(True)
            painter.setClipRect(ruler_rect)

            # 标尺背景
            painter.fillRect(ruler_rect, QBrush(COLOR_RULER_BG))

            # 刻度间隔
            tick_interval: float = self._tick_interval()

            # 刻度范围
            start_sec: float = self.scroll_offset
            end_sec: float = self.scroll_offset + content_w / pps

            # 主刻度线 + 标签
            painter.setPen(QPen(COLOR_GRID, 1))
            painter.setFont(QFont("Arial", 9))

            current_tick: float = math.ceil(start_sec / tick_interval) * tick_interval
            while current_tick <= end_sec + tick_interval:
                px: float = self._time_to_px(current_tick)
                # 刻度线
                painter.drawLine(int(px), RULER_HEIGHT - 6, int(px), RULER_HEIGHT)
                # 标签
                tick_text: str = f"{current_tick:.0f}"
                painter.setPen(QPen(COLOR_TEXT, 1))
                painter.drawText(int(px) - 6, RULER_HEIGHT - 8, 14, 16,
                                 Qt.AlignLeft | Qt.AlignVCenter, tick_text)
                painter.setPen(QPen(COLOR_GRID, 1))
                current_tick += tick_interval

            # 小刻度
            small_interval: float = tick_interval / 5
            painter.setPen(QPen(COLOR_GRID, 1))
            painter.setFont(QFont("Arial", 7))
            current_small: float = start_sec
            while current_small <= end_sec:
                px = self._time_to_px(current_small)
                painter.drawLine(int(px), RULER_HEIGHT - 3, int(px), RULER_HEIGHT)
                current_small += small_interval

            painter.setClipping(False)
        except Exception:
            pass

    def _draw_segments(self, painter: QPainter) -> None:
        """绘制所有片段矩形条。"""
        try:
            content_w: float = self._content_width()
            content_h: float = self._content_height() + RULER_HEIGHT
            painter.setClipping(True)
            painter.setClipRect(QRectF(0, RULER_HEIGHT, content_w, content_h))

            for dd in self._draw_data:
                # 根据 keep 状态选择颜色
                if dd.segment.keep:
                    base_color = COLOR_GREEN
                    highlight = COLOR_GREEN_HL
                else:
                    base_color = COLOR_RED
                    highlight = COLOR_RED_HL

                fill_color = highlight if dd.hovered else base_color
                painter.fillRect(
                    int(dd.x), int(dd.y) + RULER_HEIGHT, int(dd.w), int(dd.h),
                    QBrush(fill_color),
                )

                # 边框
                border_color = COLOR_WHITE_90 if dd.hovered else QColor(255, 255, 255, 100)
                painter.setPen(QPen(border_color, 1.5))
                painter.drawRect(
                    int(dd.x), int(dd.y) + RULER_HEIGHT, int(dd.w), int(dd.h)
                )

                # 边界指示线
                painter.setPen(QPen(COLOR_TEXT, 2))
                left_x = int(dd.x)
                painter.drawLine(left_x, int(dd.y) + RULER_HEIGHT,
                                 left_x, int(dd.y) + RULER_HEIGHT + int(dd.h))
                right_x = int(dd.x + dd.w)
                painter.drawLine(right_x, int(dd.y) + RULER_HEIGHT,
                                 right_x, int(dd.y) + RULER_HEIGHT + int(dd.h))

                # 标签文字
                if dd.w > 30:
                    painter.setPen(QPen(COLOR_TEXT, 1))
                    painter.setFont(QFont("Arial", 10, QFont.Bold))
                    painter.drawText(
                        int(dd.x), int(dd.y) + RULER_HEIGHT, int(dd.w), int(dd.h),
                        Qt.AlignCenter, dd.segment.label or "",
                    )

                # 悬停遮罩
                if dd.hovered:
                    painter.fillRect(
                        int(dd.x), int(dd.y) + RULER_HEIGHT, int(dd.w), int(dd.h),
                        QBrush(QColor(255, 255, 255, 20)),
                    )

            painter.setClipping(False)
        except Exception:
            pass

    def _draw_time_cursor(self, painter: QPainter) -> None:
        """绘制悬停片段的时间光标。"""
        try:
            if self._hover_index < 0:
                return
            dd = self._draw_data[self._hover_index]
            if dd.hovered:
                px = int(dd.x + dd.w / 2)
                painter.setPen(QPen(QColor(255, 255, 255, 180), 1, Qt.DashLine))
                painter.drawLine(
                    px, RULER_HEIGHT, px, int(dd.y + RULER_HEIGHT + dd.h)
                )
        except Exception:
            pass

    # ===========================================================================
    # 事件处理
    # ===========================================================================

    def paintEvent(self, event: QPaintEvent) -> None:
        """绘制时间线。"""
        try:
            painter: QPainter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)

            content_w = self._content_width()
            content_h = self._content_height() + RULER_HEIGHT
            painter.fillRect(QRectF(0, 0, content_w, content_h), QBrush(COLOR_BG))

            self._draw_ruler(painter)
            self._draw_segments(painter)
            self._draw_time_cursor(painter)

            painter.end()
        except Exception as exc:
            # 绘制失败时绘制简易错误指示
            painter_err = QPainter(self)
            painter_err.setPen(QPen(QColor(255, 0, 0), 2))
            painter_err.drawText(self.rect(), Qt.AlignCenter, f"Error: {exc}")
            painter_err.end()

    def resizeEvent(self, event: QResizeEvent) -> None:
        """处理窗口尺寸变化。"""
        try:
            super().resizeEvent(event)
            self._rebuild_draw_data()
            self.update()
        except Exception as exc:
            raise RuntimeError("时间线尺寸调整失败") from exc

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """处理鼠标移动事件。"""
        try:
            x_px = float(event.x())
            y_px = float(event.y())

            if y_px < RULER_HEIGHT:
                self._hover_index = -1
                self.update()
                return

            # 处理拖拽中的情况（如果正在拖拽）
            if self._dragging:
                self._handle_drag(x_px, y_px)
                return

            # body 拖拽激活判断（移动超过5像素激活）
            if self._drag_edge == "body" and not self._dragging and self._drag_index >= 0:
                # 移动超过 5 像素才激活拖拽
                if abs(x_px - self._drag_start_px) > 5:
                    self._dragging = True

            if self._dragging:
                self._handle_drag(x_px, y_px)
                return

            # 查找悬停片段
            self._hover_index = self._find_segment_at(y_px)
            self._hover_pos = QPointF(x_px, y_px)

            # 检查边界热区
            if self._hover_index >= 0 and len(self._draw_data) > self._hover_index:
                dd = self._draw_data[self._hover_index]
                if dd.left_edge.contains(QPointF(x_px, y_px)):
                    self.setCursor(Qt.SizeHorCursor)
                elif dd.right_edge.contains(QPointF(x_px, y_px)):
                    self.setCursor(Qt.SizeHorCursor)
                else:
                    self.setCursor(Qt.ArrowCursor)
            else:
                self.setCursor(Qt.ArrowCursor)

            self.update()
        except Exception:
            pass

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """处理鼠标按下事件。"""
        try:
            x_px = float(event.x())
            y_px = float(event.y())

            if y_px < RULER_HEIGHT:
                # 标尺区域点击 -> 选择时间
                time_sec = self._px_to_time(x_px)
                self.time_selected.emit(time_sec)
                return

            idx = self._find_segment_at(y_px)
            if idx >= 0 and len(self._draw_data) > idx:
                dd = self._draw_data[idx]

                # 判断点击的是哪条边
                if dd.left_edge.contains(QPointF(x_px, y_px)):
                    self._drag_edge = "left"
                    self._dragging = True
                elif dd.right_edge.contains(QPointF(x_px, y_px)):
                    self._drag_edge = "right"
                    self._dragging = True
                elif dd.x <= x_px <= dd.x + dd.w:
                    # body 区域：记录起始位置，等移动后再判断是拖拽还是点击
                    self._drag_edge = "body"
                    self._dragging = False  # 先不标记为拖拽
                else:
                    # 点击到空白区域
                    time_sec = self._px_to_time(x_px)
                    self.time_selected.emit(time_sec)
                    return

                self._drag_index = idx
                self._drag_start_px = x_px
                self._drag_start_time = self._px_to_time(x_px)
            else:
                # 空白区域点击
                time_sec = self._px_to_time(x_px)
                self.time_selected.emit(time_sec)
        except Exception:
            pass

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """处理鼠标释放事件。"""
        try:
            if self._dragging and self._drag_index >= 0:
                self._commit_drag()
            elif self._drag_edge == "body" and self._drag_index >= 0:
                # body 无拖拽 = 点击，切换 keep 状态
                dd = self._draw_data[self._drag_index]
                dd.segment.keep = not dd.segment.keep
                self.segment_toggled.emit(self._drag_index, dd.segment.keep)
                self.update()

            self._dragging = False
            self._drag_edge = ""
            self._drag_index = -1
        except Exception:
            pass

    def wheelEvent(self, event: QWheelEvent) -> None:
        """处理鼠标滚轮缩放事件。"""
        try:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_in()
            elif delta < 0:
                self.zoom_out()
        except Exception:
            pass

    def leaveEvent(self, event: QEvent) -> None:
        """鼠标离开控件时清除悬停。"""
        try:
            self._hover_index = -1
            self.update()
        except Exception:
            pass

    def sizeHint(self) -> any:
        """返回建议尺寸。"""
        return QSize(800, 200)

    # -----------------------------------------------------------------------
    # 事件处理辅助
    # -----------------------------------------------------------------------

    def _find_segment_at(self, y_px: float) -> int:
        """根据 y 像素坐标找到对应的片段索引。"""
        for idx, dd in enumerate(self._draw_data):
            top_y = dd.y + RULER_HEIGHT
            bottom_y = dd.y + RULER_HEIGHT + dd.h
            if top_y <= y_px <= bottom_y:
                return idx
        return -1

    def _handle_drag(self, x_px: float, y_px: float) -> None:
        """处理拖拽逻辑。"""
        try:
            if self._drag_index < 0 or self._drag_index >= len(self._draw_data):
                return

            dd = self._draw_data[self._drag_index]
            current_time = self._px_to_time(x_px)

            if self._drag_edge == "left":
                max_start = dd.segment.end_time - 0.1
                new_start = max(0.0, min(current_time, max_start))
                if abs(new_start - dd.segment.start_time) > 0.05:
                    dd.segment.start_time = new_start
                    self._update_draw_data_after(self._drag_index)
                    self.segment_resized.emit(
                        self._drag_index,
                        dd.segment.start_time,
                        dd.segment.end_time,
                    )

            elif self._drag_edge == "right":
                min_end = dd.segment.start_time + 0.1
                new_end = max(min_end, current_time)
                if abs(new_end - dd.segment.end_time) > 0.05:
                    dd.segment.end_time = new_end
                    self._update_draw_data_after(self._drag_index)
                    self.segment_resized.emit(
                        self._drag_index,
                        dd.segment.start_time,
                        dd.segment.end_time,
                    )

            elif self._drag_edge == "body":
                time_delta = current_time - self._drag_start_time
                new_start = dd.segment.start_time + time_delta
                new_end = dd.segment.end_time + time_delta

                total_dur = self.project.duration if self.project else 999999
                if new_start < 0:
                    new_start = 0.0
                    new_end = dd.segment.duration()
                if new_end > total_dur:
                    new_end = total_dur
                    new_start = new_end - dd.segment.duration()

                if abs(new_start - dd.segment.start_time) > 0.05 or \
                   abs(new_end - dd.segment.end_time) > 0.05:
                    dd.segment.start_time = new_start
                    dd.segment.end_time = new_end
                    self._update_draw_data_after(self._drag_index)
                    self.segment_resized.emit(
                        self._drag_index,
                        dd.segment.start_time,
                        dd.segment.end_time,
                    )

                self._drag_start_time = current_time

            self.update()
        except Exception:
            pass

    def _update_draw_data_after(self, start_index: int) -> None:
        """更新 start_index 之后的绘制数据 x 坐标。"""
        try:
            if start_index < 0 or start_index >= len(self._draw_data):
                return

            pps = BASE_PPS * self.zoom_level

            first_dd = self._draw_data[start_index]
            first_dd.x = first_dd.segment.start_time * pps

            for i in range(start_index + 1, len(self._draw_data)):
                curr_dd = self._draw_data[i]
                curr_dd.x = curr_dd.segment.start_time * pps
                curr_dd.w = max(
                    MIN_SEGMENT_WIDTH,
                    (curr_dd.segment.end_time - curr_dd.segment.start_time) * pps
                )
                curr_dd.left_edge = QRectF(curr_dd.x, curr_dd.y, BORDER_HIT_WIDTH, curr_dd.h)
                curr_dd.right_edge = QRectF(
                    curr_dd.x + curr_dd.w - BORDER_HIT_WIDTH,
                    curr_dd.y, BORDER_HIT_WIDTH, curr_dd.h
                )
        except Exception:
            pass

    def _commit_drag(self) -> None:
        """提交拖拽结果并发射 segment_resized 信号。"""
        try:
            if self._drag_index < 0 or self._drag_index >= len(self._draw_data):
                return

            dd = self._draw_data[self._drag_index]

            # 边界修正
            if dd.segment.start_time >= dd.segment.end_time:
                mid = (dd.segment.start_time + dd.segment.end_time) / 2.0
                if self._drag_edge in ("left", "body"):
                    dd.segment.start_time = mid - 0.05
                else:
                    dd.segment.end_time = mid + 0.05

            self.segment_resized.emit(
                self._drag_index,
                dd.segment.start_time,
                dd.segment.end_time,
            )
        except Exception:
            pass

    # ===========================================================================
    # 辅助属性
    # ===========================================================================

    @property
    def duration_label(self) -> str:
        """获取当前总时长标签文本。"""
        return self._duration_label


# ===========================================================================
# 入口自检
# ===========================================================================

if __name__ == "__main__":
    import sys
    from PyQt5.QtWidgets import QApplication

    app: QApplication = QApplication(sys.argv)

    # 创建测试数据
    test_segments: List[Segment] = [
        Segment(0.0, 3.5, True, "片头"),
        Segment(3.5, 8.0, False, "静音"),
        Segment(8.0, 15.0, True, "精彩"),
        Segment(15.0, 18.5, True, "过渡"),
        Segment(18.5, 22.0, False, "片尾"),
        Segment(22.0, 30.0, True, "正片"),
    ]

    test_project: Project = Project(
        video_path="test_video.mp4",
        segments=test_segments,
        fps=30.0,
        width=1920,
        height=1080,
        duration=30.0,
    )

    widget: TimelineWidget = TimelineWidget()
    widget.setWindowTitle("AI 视频剪辑 - 时间线")
    widget.set_project(test_project)
    widget.resize(900, 350)
    widget.show()

    sys.exit(app.exec_())