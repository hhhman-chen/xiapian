"""
AI 视频剪辑桌面软件 - 视频预览组件 (VideoPlayer)

提供基于 PyQt5 的视频预览 QWidget，支持播放/暂停、进度拖动、
时间显示和键盘快捷键，可被 main.py 直接嵌入布局使用。

不依赖 config/models/editor_core/ai_analyzer 模块，
仅使用 PyQt5 + OpenCV + 标准库，保证独立的 QWidget 组件。

接口概览:
    - set_project(project: Project)  → 加载新视频
    - play() / pause() / toggle_play()
    - seek_to(time_sec: float)      → 跳到指定时间
    - get_current_time() -> float    → 返回当前播放位置

主要类:
    VideoPlayer: 继承 QWidget，封装视频预览的全部 UI 与逻辑。
"""

from __future__ import annotations

import math
import os
import sys
import logging
from typing import Any, Optional

import cv2
import numpy as np
from PyQt5.QtCore import Qt, QSize, QTimer  # type: ignore
from PyQt5.QtGui import QImage, QPixmap  # type: ignore
from PyQt5.QtWidgets import (  # type: ignore
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

# ─── 模块级日志 ──

logger: logging.Logger = logging.getLogger(__name__)

# ─── 常量 ──

_DEFAULT_FPS: int = 30
_DEFAULT_WIDTH: int = 1920
_DEFAULT_HEIGHT: int = 1080
_TIME_FORMAT: str = "%M:%S"  # MM:SS 格式


# ─── 辅助函数 ──

def _format_time(seconds: float) -> str:
    """
    将秒数格式化为 MM:SS 字符串。

    Parameters
    ----------
    seconds : float
        时间秒数。

    Returns
    -----
    str
        格式如 "01:23" 的时间字符串。
    """
    mins: int = int(seconds) // 60
    secs: int = int(seconds) % 60
    return f"{mins:02d}:{secs:02d}"


def _bgr_to_qimage(
    bgr_frame: np.ndarray,
    width: int,
    height: int,
) -> QImage:
    """
    将 OpenCV BGR numpy 数组转换为 PyQt5 QImage。

    OpenCV 默认使用 BGR 通道顺序，QImage.Format_RGB888
    使用 RGB 顺序，因此需要交换通道。

    Parameters
    ----------
    bgr_frame : np.ndarray
        BGR 格式的 numpy 数组，dtype=uint8。
    width : int
        视频宽度（像素）。
    height : int
        视频高度（像素）。

    Returns
    -----
    QImage
        转换后的 QImage 对象。
    """
    # 交换 BGR → RGB
    rgb_frame: np.ndarray = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
    # 转为连续内存布局，避免 QImage 构造时复制数据
    rgb_frame = np.ascontiguousarray(rgb_frame)
    return QImage(
        rgb_frame.data,
        width,
        height,
        rgb_frame.strides[0],
        QImage.Format_RGB888,
    )


# ─── VideoPlayer 主类 ──

class VideoPlayer(QWidget):
    """
    视频预览组件，继承 QWidget。

    功能:
        - 使用 OpenCV 读取视频帧，转为 QImage → QPixmap 显示在 QLabel 上
        - 播放 / 暂停按钮（toggle QPushButton）
        - 进度条（QSlider），支持拖拽跳转
        - 当前时间 / 总时长 标签（MM:SS 格式）
        - QTimer 控制帧刷新（每 1000/fps 毫秒刷新一次）
        - 键盘快捷键：空格=播放/暂停，左右箭头=前后 5 秒

    示例用法:
        >>> player = VideoPlayer()
        >>> player.set_project(project)
        >>> player.play()
        >>> layout.addWidget(player)
    """

    # ─── 自定义信号（通过 PyQt5 信号机制） ──
    # 播放状态变化时发出
    # 当前时间更新时发出

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        fps: int = _DEFAULT_FPS,
        video_width: int = _DEFAULT_WIDTH,
        video_height: int = _DEFAULT_HEIGHT,
    ) -> None:
        """
        初始化 VideoPlayer 组件。

        Parameters
        ----------
        parent : QWidget, optional
            父 widget。
        fps : int
            视频帧率，用于计算 QTimer 刷新间隔。
        video_width : int
            视频宽度（像素），影响显示尺寸。
        video_height : int
            视频高度（像素），影响显示尺寸。
        """
        super().__init__(parent)

        # ─── 内部状态 ──
        self._fps: int = fps
        self._video_width: int = video_width
        self._video_height: int = video_height
        self._video_path: str = ""
        self._duration: float = 0.0
        self._current_time: float = 0.0
        self._is_playing: bool = False
        self._cap: Optional[cv2.VideoCapture] = None
        self._frame_interval: float = 1000.0 / fps if fps > 0 else 33.33

        # ─── UI 组件 ──
        self._setup_ui()

        # ─── 定时器 ──
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_timer_tick)
        self._timer.start(int(self._frame_interval))

        # ─── 状态同步 ──
        self._slider.blockSignals(True)
        self._slider.setValue(0)
        self._slider.blockSignals(False)

        logger.debug(
            "VideoPlayer 初始化: fps=%d, size=%dx%d, interval=%.1fms",
            self._fps, self._video_width, self._video_height, self._frame_interval,
        )

    # ─── UI 构建 ──

    def _setup_ui(self) -> None:
        """构建界面布局：视频显示 + 控制栏。"""
        # 主垂直布局
        main_layout: QVBoxLayout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        # ── 视频显示区 ──
        self._video_label = QLabel(self)
        self._video_label.setMinimumSize(self._video_width, self._video_height)
        self._video_label.setAlignment(Qt.AlignCenter)
        self._video_label.setStyleSheet(
            "QLabel { background-color: #1a1a2e; border: 1px solid #444; }"
        )
        self._video_label.setText("等待视频加载...")
        main_layout.addWidget(self._video_label)

        # ── 控制区 ──
        control_layout: QHBoxLayout = QHBoxLayout()
        control_layout.setContentsMargins(0, 0, 0, 0)
        control_layout.setSpacing(8)

        # 播放/暂停按钮（不自锁，由程序控制文字）
        self._play_button = QPushButton("▶ 播放", self)
        self._play_button.clicked.connect(self.toggle_play)
        self._play_button.setMinimumSize(100, 36)
        self._play_button.setStyleSheet(
            "QPushButton { font-size: 14px; padding: 4px 12px; }"
        )
        control_layout.addWidget(self._play_button)

        # 时间标签
        self._time_label = QLabel("00:00 / 00:00", self)
        self._time_label.setMinimumSize(160, 36)
        self._time_label.setStyleSheet("QLabel { font-size: 13px; color: #ccc; }")
        control_layout.addWidget(self._time_label)
        control_layout.addStretch()  # 弹性空间

        main_layout.addLayout(control_layout)

        # 进度条
        self._slider = QSlider(Qt.Horizontal, self)
        self._slider.setMinimum(0)
        self._slider.setMaximum(10000)  # 最大 10000 代表 100%
        self._slider.valueChanged.connect(self._on_slider_changed)
        self._slider.setMinimumHeight(24)
        main_layout.addWidget(self._slider)

    # ─── 核心接口 ──

    def set_project(
        self,
        project: Any,
    ) -> None:
        """
        加载新项目视频。

        从 project 对象中读取 video_path、fps、width、height、duration
        并初始化视频捕获器。

        Parameters
        ----------
        project : Any
            支持以下属性的对象：
            - video_path (str): 视频文件路径
            - fps (float): 帧率
            - width (int): 宽度
            - height (int): 高度
            - duration (float): 总时长（秒）
        """
        try:
            # 释放旧的视频捕获器
            self._release_cap()

            # 从 project 获取属性（duck typing，不依赖具体模块）
            self._video_path = str(getattr(project, "video_path", ""))
            self._fps = int(getattr(project, "fps", _DEFAULT_FPS))
            self._video_width = int(getattr(project, "width", _DEFAULT_WIDTH))
            self._video_height = int(getattr(project, "height", _DEFAULT_HEIGHT))
            self._duration = float(getattr(project, "duration", 0.0))

            if not self._video_path:
                raise ValueError("project.video_path 不能为空")

            # 更新 FPS 和显示尺寸
            self._timer.stop()
            self._frame_interval = 1000.0 / self._fps if self._fps > 0 else 33.33
            self._timer.start(int(self._frame_interval))

            self._video_label.setMinimumSize(self._video_width, self._video_height)

            # 初始化视频捕获器
            self._cap = cv2.VideoCapture(self._video_path)
            if not self._cap.isOpened():
                raise cv2.error(f"无法打开视频文件: {self._video_path}")

            # 更新当前时间标签
            self._current_time = 0.0
            self._update_time_display()
            self._update_slider_max()

            logger.info(
                "项目加载成功: path=%s, fps=%d, size=%dx%d, duration=%.2fs",
                self._video_path, self._fps, self._video_width,
                self._video_height, self._duration,
            )

        except Exception as exc:
            logger.error("加载项目失败: %s", exc)
            raise

    def play(self) -> None:
        """开始播放视频。"""
        try:
            if self._cap is None or not self._cap.isOpened():
                logger.warning("视频捕获器未初始化，无法播放")
                return

            self._is_playing = True
            self._play_button.setText("■ 暂停")
            self._timer.start(int(self._frame_interval))
            logger.debug("播放开始")
        except Exception as exc:
            logger.error("播放时出错: %s", exc)
            raise

    def pause(self) -> None:
        """暂停视频播放。"""
        try:
            self._is_playing = False
            self._play_button.setText("▶ 播放")
            self._timer.stop()
            logger.debug("播放暂停，当前时间=%.2fs", self._current_time)
        except Exception as exc:
            logger.error("暂停时出错: %s", exc)
            raise

    def toggle_play(self) -> None:
        """切换播放/暂停状态。"""
        if self._is_playing:
            self.pause()
        else:
            self.play()

    def seek_to(self, time_sec: float) -> None:
        """
        跳转到指定时间点。

        Parameters
        ----------
        time_sec : float
            目标时间（秒），会在 [0, duration] 范围内钳制。
        """
        try:
            if self._cap is None or not self._cap.isOpened():
                logger.warning("视频捕获器未初始化，无法跳转")
                return

            # 钳制时间范围
            time_sec = max(0.0, min(time_sec, self._duration))
            self._current_time = time_sec

            # 设置视频捕获器位置
            frame_idx: int = int(time_sec * self._fps)
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)

            # 更新 UI
            self._update_slider_value()
            self._update_time_display()

            # 立即刷新当前帧
            self._refresh_frame()

            logger.debug("跳转到: %.2fs (frame=%d)", time_sec, frame_idx)
        except Exception as exc:
            logger.error("跳转时出错: %s", exc)
            raise

    def get_current_time(self) -> float:
        """
        获取当前播放位置（秒）。

        Returns
        -----
        float
            当前时间，范围 [0, duration]。
        """
        return self._current_time

    # ─── 尺寸管理 ──

    def set_minimum_size(
        self,
        width: int,
        height: int,
    ) -> None:
        """
        设置最小显示尺寸。

        Parameters
        ----------
        width : int
            最小宽度（像素）。
        height : int
            最小高度（像素）。
        """
        self._video_width = width
        self._video_height = height
        self._video_label.setMinimumSize(width, height)

    def set_size(
        self,
        width: int,
        height: int,
    ) -> None:
        """
        设置精确显示尺寸。

        Parameters
        ----------
        width : int
            宽度（像素）。
        height : int
            高度（像素）。
        """
        self.set_minimum_size(width, height)
        self._video_label.resize(width, height)
        self._video_label.setFixedSize(width, height)

    def update_size(
        self,
        width: int,
        height: int,
    ) -> None:
        """
        更新显示尺寸（最小 + 当前尺寸都更新）。

        Parameters
        ----------
        width : int
            宽度（像素）。
        height : int
            高度（像素）。
        """
        self.set_size(width, height)

    def resizeEvent(
        self,
        event: Any,  # PyQt5 QResizeEvent
    ) -> None:
        """响应窗口大小变化，保持视频标签居中。"""
        super().resizeEvent(event)  # type: ignore
        if self._video_label is not None:
            self._video_label.setAlignment(Qt.AlignCenter)

    # ─── 键盘事件 ──

    def keyPressEvent(self, event: Any) -> None:  # PyQt5 QKeyEvent
        """
        响应键盘事件。

        快捷键:
            - 空格: 播放/暂停
            - 左箭头: 后退 5 秒
            - 右箭头: 前进 5 秒
        """
        try:
            key: int = event.key()  # type: ignore

            if key == Qt.Key_Space:
                self.toggle_play()
            elif key == Qt.Key_Left:
                # 左右箭头 = 前后 5 秒
                self.seek_to(self._current_time - 5.0)
            elif key == Qt.Key_Right:
                self.seek_to(self._current_time + 5.0)
        except Exception as exc:
            logger.error("键盘事件处理错误: %s", exc)
            super().keyPressEvent(event)  # type: ignore

    # ─── 私有方法 ──

    def _on_timer_tick(self) -> None:
        """
        QTimer 定时回调，播放模式下刷新视频帧。

        非播放模式（暂停）下不刷新，节省 CPU。
        """
        if not self._is_playing:
            return

        try:
            if self._cap is None or not self._cap.isOpened():
                return

            # 到达视频末尾时，停止播放而非循环
            if self._current_time >= self._duration:
                self.pause()
                self._current_time = self._duration  # 停在末尾
                self._update_time_display()
                return

            # 前进到下一帧
            self._current_time += self._frame_interval / 1000.0
            if self._current_time > self._duration:
                self._current_time = self._duration

            frame_idx: int = int(self._current_time * self._fps)
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = self._cap.read()

            if ret and frame is not None and frame.size > 0:
                # 直接使用刚读到的帧，避免 _refresh_frame 再次读取造成跳帧
                self._show_frame(frame)
                self._update_time_display()
                self._update_slider_value()
        except Exception as exc:
            logger.debug("定时器帧刷新: %s", exc)

    def _show_frame(self, frame: np.ndarray) -> None:
        """直接渲染给定帧到 QLabel（不重新读取视频文件）。

        用于定时器回调中直接使用刚读到的帧，避免 _refresh_frame
        再次读取 cap 导致跳帧。
        """
        try:
            qimage: QImage = _bgr_to_qimage(frame, self._video_width, self._video_height)
            pixmap: QPixmap = QPixmap.fromImage(qimage)
            self._video_label.setPixmap(pixmap.scaled(
                self._video_label.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            ))
        except Exception as exc:
            logger.debug("帧渲染错误: %s", exc)

    def _refresh_frame(self) -> None:
        """将当前帧渲染到 QLabel 上。"""
        if self._cap is None or not self._cap.isOpened():
            return

        ret: bool = False
        frame: Optional[np.ndarray] = None
        for _ in range(3):
            ret, frame = self._cap.read()
            if ret and frame is not None and frame.size > 0:
                break

        if not ret or frame is None or frame.size == 0:
            return

        try:
            # OpenCV BGR → QImage → QPixmap → QLabel
            qimage: QImage = _bgr_to_qimage(frame, self._video_width, self._video_height)
            pixmap: QPixmap = QPixmap.fromImage(qimage)
            self._video_label.setPixmap(pixmap.scaled(
                self._video_label.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            ))
        except Exception as exc:
            logger.debug("帧渲染错误: %s", exc)

    def _on_slider_changed(self, value: int) -> None:
        """
        QSlider 值变化回调，跳转到对应时间点。

        Parameters
        ----------
        value : int
            滑动条的当前值 (0 ~ 10000)。
        """
        try:
            # 将 0-10000 映射到 0-duration 秒
            if self._duration > 0:
                time_sec: float = (value / 10000.0) * self._duration
            else:
                time_sec = 0.0
            self.seek_to(time_sec)
        except Exception as exc:
            logger.debug("滑块变化处理: %s", exc)

    def _update_time_display(self) -> None:
        """更新当前时间 / 总时长 标签（MM:SS 格式）。"""
        current_str: str = _format_time(self._current_time)
        total_str: str = _format_time(self._duration)
        self._time_label.setText(f"{current_str} / {total_str}")

    def _update_slider_value(self) -> None:
        """将当前时间映射到滑动条值（0-10000），阻塞信号避免反馈环。"""
        if self._duration > 0:
            value: int = int((self._current_time / self._duration) * 10000)
        else:
            value = 0
        self._slider.blockSignals(True)
        self._slider.setValue(value)
        self._slider.blockSignals(False)

    def _update_slider_max(self) -> None:
        """确保滑动条最大值与视频时长匹配。"""
        if self._duration > 0:
            self._slider.setMaximum(10000)
        else:
            self._slider.setMaximum(1000)

    def _release_cap(self) -> None:
        """安全释放 OpenCV VideoCapture 资源。"""
        try:
            if self._cap is not None:
                if self._cap.isOpened():
                    self._cap.release()
                self._cap = None
        except Exception as exc:
            logger.debug("释放捕获器: %s", exc)

    def closeEvent(self, event: Any) -> None:  # PyQt5 QCloseEvent
        """关闭事件，释放视频资源。"""
        try:
            self._timer.stop()
            self._release_cap()
            logger.debug("VideoPlayer 已关闭，资源已释放")
        except Exception as exc:
            logger.debug("关闭事件处理: %s", exc)
        super().closeEvent(event)  # type: ignore

    # ─── 属性访问 ──

    @property
    def current_time(self) -> float:
        """当前播放位置（秒）。"""
        return self._current_time

    @property
    def is_playing(self) -> bool:
        """是否正在播放。"""
        return self._is_playing

    @property
    def video_path(self) -> str:
        """当前视频文件路径。"""
        return self._video_path

    @property
    def fps(self) -> int:
        """当前帧率。"""
        return self._fps

    @property
    def duration(self) -> float:
        """视频总时长（秒）。"""
        return self._duration


# ─── 模块导出 ──

__all__: list[str] = [
    "VideoPlayer",
    "_format_time",
    "_bgr_to_qimage",
    "_DEFAULT_FPS",
    "_DEFAULT_WIDTH",
    "_DEFAULT_HEIGHT",
    "_TIME_FORMAT",
]
