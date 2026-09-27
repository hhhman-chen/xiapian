"""
AI 视频剪辑编辑器 - 主窗口入口模块

提供 QMainWindow 主窗口，集成播放器、片段列表、时间线和分析/导出功能。

布局结构:
    ├── 菜单栏: 文件 / AI
    ├── 左: VideoPlayer (播放器 + 控制)
    ├── 右: QListWidget (片段列表)
    ├── 中下: TimelineWidget (时间线可视化)
    └── 底部: 按钮 + 进度条 + 状态栏

运行:
    python -m video-editor.main    (项目根目录下)
    python main.py                 (在 video-editor/ 目录下)
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

# ─── Qt 导入 ──

from PyQt5.QtCore import QThread, Qt, pyqtSignal  # type: ignore
from PyQt5.QtGui import QFont  # type: ignore
from PyQt5.QtWidgets import (  # type: ignore
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QMenuBar,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

# ─── 同项目模块导入 ──

import config
import editor_core
import models
from ai_analyzer import analyze_video
from player import VideoPlayer
from timeline_widget import TimelineWidget

# ─── 模块级日志 ──

logger: logging.Logger = logging.getLogger(__name__)

# ─── 常量 ──

_DEFAULT_WINDOW_SIZE: tuple[int, int] = (650, 850)  # 默认窗口尺寸 (宽, 高)
_PLAYER_MIN_WIDTH: int = 320                         # 播放器最小宽度
_PLAYER_MAX_WIDTH: int = 500                         # 播放器最大宽度
_SEGMENT_HEIGHT_STR: str = "18px"                    # 片段列表项高度


# ─── 辅助函数 ──

def _format_time(seconds: float) -> str:
    """将秒数格式化为 MM:SS 字符串。"""
    mins: int = int(seconds) // 60
    secs: int = int(seconds) % 60
    return f"{mins:02d}:{secs:02d}"


def _format_duration(seconds: float) -> str:
    """将秒数格式化为带单位的字符串，如 30.0s。"""
    if seconds >= 60:
        mins = int(seconds) // 60
        secs = seconds % 60
        return f"{mins}分{secs:.1f}秒"
    return f"{seconds:.1f}s"


def _get_file_icon_path() -> str:
    """返回项目目录中文件图标路径（如果存在）。"""
    icon_path: str = str(Path(__file__).parent / "icon.png")
    return icon_path


def _create_reset_button(text: str = "按钮") -> QPushButton:
    """创建带 DS_ResetButton 样式的按钮。

    Returns:
        样式化后的 QPushButton 实例。
    """
    btn: QPushButton = QPushButton(text)
    btn.setObjectName("DS_ResetButton")
    btn.setStyleSheet(
        "QPushButton#DS_ResetButton {"
        "    background-color: #3a3a5c;"
        "    color: #e0e0e0;"
        "    border: 1px solid #555;"
        "    border-radius: 4px;"
        "    padding: 6px 16px;"
        "    font-size: 13px;"
        "}"
        "QPushButton#DS_ResetButton:hover {"
        "    background-color: #4a4a7c;"
        "    border-color: #888;"
        "}"
        "QPushButton#DS_ResetButton:pressed {"
        "    background-color: #2a2a4c;"
        "}"
    )
    return btn


# ─── 分析工作线程 ──


class _AnalysisWorker(QThread):
    """AI 分析工作线程。

    在后台调用 analyze_video，完成后通过 finished 信号将结果传给主线程。
    """

    finished: Any = pyqtSignal(list)  # 分析完成的信号
    error_occurred: Any = pyqtSignal(str)  # 分析出错的信号

    def __init__(self, project: models.Project) -> None:
        """初始化工作线程。

        Args:
            project: 需要分析的视频剪辑项目。
        """
        super().__init__()
        self._project: models.Project = project

    def run(self) -> None:
        """执行 AI 分析（阻塞调用）。"""
        try:
            segments: list[models.Segment] = analyze_video(self._project)
            self.finished.emit(segments)
        except Exception as exc:
            logger.error("AI 分析线程异常: %s", exc)
            # 发射错误信号，传递错误信息
            self.error_occurred.emit(str(exc))


class _ExportWorker(QThread):
    """视频导出工作线程。

    在后台调用 editor_core.export_video，通过信号报告进度和结果。
    避免 moviepy 编码在主线程阻塞 UI。
    """

    progress: Any = pyqtSignal(int)   # 进度信号 (0-100)
    finished: Any = pyqtSignal(str, str)  # (output_path, error)

    def __init__(self, project: models.Project) -> None:
        super().__init__()
        self._project: models.Project = project

    def run(self) -> None:
        try:
            self.progress.emit(40)
            output_path: str = editor_core.export_video(self._project)
            self.progress.emit(90)
            self.finished.emit(output_path, "")
        except Exception as exc:
            error_msg: str = str(exc)
            logger.error("导出线程异常: %s", error_msg)
            self.finished.emit("", error_msg)


# ─── 主窗口 ──


class MainWindow(QMainWindow):
    """AI 视频剪辑编辑器主窗口。

    集成播放器、片段列表、时间线，提供完整的剪辑工作流。
    """

    def __init__(self) -> None:
        """初始化主窗口。"""
        super().__init__()
        self._project: Optional[models.Project] = None

        # 分析线程（复用同一实例，避免重复创建）
        self._analysis_worker: Optional[_AnalysisWorker] = None

        self._setup_ui()

    # ─── UI 构建 ──

    def _setup_ui(self) -> None:
        """构建完整的界面布局。"""
        self.setWindowTitle("AI 视频剪辑编辑器")
        self.resize(*_DEFAULT_WINDOW_SIZE)

        # 创建中央容器和主布局
        central_widget: QWidget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout: QVBoxLayout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        # ── 菜单栏 ──
        self._create_menu_bar()

        # ── 中间区域 (播放器 + 片段列表) ──
        self._create_middle_area(main_layout)

        # ── 时间线 ──
        self._timeline = TimelineWidget()
        main_layout.addWidget(self._timeline)

        # ── 按钮 + 进度条 ──
        self._create_button_area(main_layout)

        # ── 状态栏 ──
        self._create_status_bar()

        # ── 信号连接 ──
        self._connect_signals()

    # ─── 菜单栏 ──

    def _create_menu_bar(self) -> None:
        """创建菜单栏。"""
        menu_bar: QMenuBar = self.menuBar()

        # 文件菜单
        file_menu = menu_bar.addMenu("文件")
        file_menu.setObjectName("DS_ResetButton")

        # 打开视频 (Ctrl+O)
        open_action = file_menu.addAction("打开视频")
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self._on_open_video)

        # 保存项目 (Ctrl+S)
        save_action = file_menu.addAction("保存项目")
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self._on_save_project)

        # 导出视频 (Ctrl+E)
        export_action = file_menu.addAction("导出视频")
        export_action.setShortcut("Ctrl+E")
        export_action.triggered.connect(self._on_export_video)

        # 退出 (Ctrl+Q)
        exit_action = file_menu.addAction("退出")
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)

        # AI 菜单
        ai_menu = menu_bar.addMenu("AI")
        ai_menu.setObjectName("DS_ResetButton")

        # AI 分析
        analyze_action = ai_menu.addAction("分析")
        analyze_action.triggered.connect(self._on_ai_analyze)

        # AI 设置
        settings_action = ai_menu.addAction("设置")
        settings_action.triggered.connect(self._on_ai_settings)

    # ─── 中间区域 ──

    def _create_middle_area(self, parent_layout: QVBoxLayout) -> None:
        """创建播放器 + 片段列表的中间区域。"""
        middle_widget: QWidget = QWidget(self)
        middle_layout: QHBoxLayout = QHBoxLayout(middle_widget)
        middle_layout.setContentsMargins(0, 0, 0, 4)
        middle_layout.setSpacing(8)

        # 左侧: VideoPlayer
        self._player = VideoPlayer()
        self._player.setObjectName("DS_ResetButton")
        self._player.setMinimumWidth(_PLAYER_MIN_WIDTH)
        self._player.setMaximumWidth(_PLAYER_MAX_WIDTH)
        self._player.setMinimumSize(_PLAYER_MIN_WIDTH, 400)
        middle_layout.addWidget(self._player, stretch=0)

        # 右侧: 片段列表
        right_widget: QWidget = QWidget(self)
        right_layout: QVBoxLayout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)

        # 片段列表标题
        list_title: QLabel = QLabel("片段列表")
        list_title.setStyleSheet(
            "QLabel { font-size: 13px; color: #aaa; padding: 2px 4px; }"
        )
        right_layout.addWidget(list_title)

        # 片段 QListWidget
        self._segment_list: QListWidget = QListWidget()
        self._segment_list.setObjectName("DS_ResetButton")
        self._segment_list.setStyleSheet(
            "QListWidget#DS_ResetButton {"
            "    background-color: #2a2a3e;"
            "    border: 1px solid #444;"
            "    border-radius: 4px;"
            "    font-size: 13px;"
            "    color: #e0e0e0;"
            "}"
            "QListWidget#DS_ResetButton::item {"
            "    height: 22px;"
            "    padding: 2px 6px;"
            "    border-bottom: 1px solid #3a3a5c;"
            "}"
            "QListWidget#DS_ResetButton::item:hover {"
            "    background-color: #3a3a5c;"
            "}"
            "QListWidget#DS_ResetButton::item:selected {"
            "    background-color: #4a4a7c;"
            "}"
        )
        self._segment_list.setMinimumSize(200, 300)
        self._segment_list.setSelectionMode(QListWidget.SingleSelection)
        right_layout.addWidget(self._segment_list)

        middle_layout.addWidget(right_widget, stretch=1)
        parent_layout.addWidget(middle_widget)

    # ─── 按钮 + 进度条 ──

    def _create_button_area(self, parent_layout: QVBoxLayout) -> None:
        """创建底部按钮和进度条区域。"""
        btn_widget: QWidget = QWidget(self)
        btn_layout: QHBoxLayout = QHBoxLayout(btn_widget)
        btn_layout.setContentsMargins(0, 4, 0, 4)
        btn_layout.setSpacing(8)

        # AI 分析按钮
        self._ai_analyze_btn: QPushButton = _create_reset_button("AI 分析")
        self._ai_analyze_btn.setObjectName("DS_ResetButton")
        btn_layout.addWidget(self._ai_analyze_btn)

        # 导出视频按钮
        self._export_btn: QPushButton = _create_reset_button("导出视频")
        self._export_btn.setObjectName("DS_ResetButton")
        btn_layout.addWidget(self._export_btn)

        btn_layout.addStretch()  # 弹性空间

        # 进度条
        self._progress_bar: QProgressBar = QProgressBar(self)
        self._progress_bar.setObjectName("DS_ResetButton")
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setStyleSheet(
            "QProgressBar#DS_ResetButton {"
            "    background-color: #1a1a2e;"
            "    border: 1px solid #444;"
            "    border-radius: 3px;"
            "    text-align: center;"
            "    color: #ccc;"
            "    height: 16px;"
            "}"
            "QProgressBar#DS_ResetButton::chunk {"
            "    background-color: #4a4a7c;"
            "    border-radius: 2px;"
            "}"
        )
        self._progress_bar.setValue(0)
        self._progress_bar.setMinimumWidth(160)
        btn_layout.addWidget(self._progress_bar)

        parent_layout.addWidget(btn_widget)

    # ─── 状态栏 ──

    def _create_status_bar(self) -> None:
        """创建状态栏。"""
        self.status_bar: QStatusBar = self.statusBar()
        self.status_bar.setObjectName("DS_ResetButton")
        self.status_bar.setStyleSheet(
            "QStatusBar#DS_ResetButton { color: #aaa; font-size: 12px; }"
        )

        # 状态标签
        self._file_label: QLabel = QLabel("未打开文件")
        self._duration_label: QLabel = QLabel("时长: --")
        self._fps_label: QLabel = QLabel("帧率: --")
        self._resolution_label: QLabel = QLabel("分辨率: --")

        self.status_bar.addWidget(self._file_label)
        self.status_bar.addPermanentWidget(self._duration_label)
        self.status_bar.addPermanentWidget(self._fps_label)
        self.status_bar.addPermanentWidget(self._resolution_label)

    # ─── 信号连接 ──

    def _connect_signals(self) -> None:
        """连接各组件的信号。"""
        # 时间线 → 播放器: 点击时间线 → 跳转到对应时间
        self._timeline.time_selected.connect(self._on_timeline_time_selected)

        # 时间线 → 片段列表: 片段切换 → 同步录像
        self._timeline.segment_toggled.connect(self._on_segment_toggled)

        # 片段列表 → 时间线: 点击列表项 → 高亮时间线片段
        self._segment_list.currentRowChanged.connect(self._on_segment_list_changed)

        # 按钮
        self._ai_analyze_btn.clicked.connect(self._on_ai_analyze)
        self._export_btn.clicked.connect(self._on_export_video)

    # ─── 核心流程 ──

    def _on_open_video(self) -> None:
        """处理打开视频: 文件对话框 → 加载 → 同步 UI。"""
        try:
            # 打开文件对话框
            file_path: str = QFileDialog.getOpenFileName(
                self,
                "打开视频文件",
                config.PROJECT_DIR.as_posix(),
                "视频文件 (*.mp4 *.mov *.avi *.mkv);;所有文件 (*.*)",
            )[0]

            if not file_path:
                return

            # 加载视频
            self._project = editor_core.load_video(file_path)

            # 同步到播放器和时间线
            self._player.set_project(self._project)
            self._timeline.set_project(self._project)

            # 更新片段列表
            self._update_segment_list()

            # 更新状态栏
            self._update_status_bar()

            logger.info("视频打开成功: %s", file_path)

        except FileNotFoundError as exc:
            logger.error("文件不存在: %s", exc)
            self._show_status("文件不存在: " + str(exc))
        except Exception as exc:
            logger.error("打开视频失败: %s", exc)
            self._show_status("打开视频失败: " + str(exc))

    def _on_save_project(self) -> None:
        """处理保存项目: 文件对话框 → 写入 JSON。"""
        try:
            if self._project is None:
                self._show_status("没有项目可保存")
                return

            # 打开保存对话框
            default_name: str = (
                Path(self._project.video_path).stem + "_project.json"
            )
            file_path: str = QFileDialog.getSaveFileName(
                self,
                "保存项目",
                default_name,
                "项目文件 (*.json);;所有文件 (*.*)",
            )[0]

            if not file_path:
                return

            # 写入 JSON
            json_str: str = self._project.to_json()
            Path(file_path).write_text(json_str, encoding="utf-8")

            self._show_status("项目已保存: " + file_path)

        except Exception as exc:
            logger.error("保存项目失败: %s", exc)
            self._show_status("保存项目失败: " + str(exc))

    def _on_export_video(self) -> None:
        """处理导出视频: 获取片段 → 裁剪 → 导出 → 通知。"""
        try:
            if self._project is None:
                self._show_status("没有可导出的视频")
                return

            # 获取时间线修改后的片段
            modified_segments: list[models.Segment] = (
                self._timeline.get_modified_segments()
            )
            segments_to_remove: list[models.Segment] = [
                seg for seg in modified_segments if not seg.keep
            ]

            # 先做快速裁剪（主线程 OK），再做耗时导出（子线程）
            editor_core.cut_segments(self._project, segments_to_remove)

            # 禁用按钮，启动导出线程
            self._export_btn.setEnabled(False)
            self._export_btn.setText("导出中...")
            self._progress_bar.setValue(10)

            self._export_worker = _ExportWorker(self._project)
            self._export_worker.progress.connect(self._on_export_progress)
            self._export_worker.finished.connect(self._on_export_finished)
            self._export_worker.start()

        except Exception as exc:
            logger.error("导出视频失败: %s", exc)
            self._show_status("导出失败: " + str(exc))
            self._export_btn.setEnabled(True)
            self._export_btn.setText("导出视频")

    def _on_export_progress(self, value: int) -> None:
        """导出进度更新。"""
        self._progress_bar.setValue(value)

    def _on_export_finished(self, output_path: str, error: str) -> None:
        """导出完成回调。"""
        self._export_btn.setEnabled(True)
        self._export_btn.setText("导出视频")
        self._progress_bar.setValue(100)
        if error:
            self._show_status("导出失败: " + error)
            logger.error("导出视频失败: %s", error)
        else:
            self._show_status("导出完成: " + output_path)

    def _on_ai_analyze(self) -> None:
        """处理 AI 分析: 禁用 UI → 启动线程 → 更新 timeline → 启用 UI。"""
        try:
            if self._project is None:
                self._show_status("请先打开视频")
                return

            # 重入保护：防止快速点击创建孤立 Worker
            if self._analysis_worker is not None and self._analysis_worker.isRunning():
                self._show_status("AI 分析已在运行中")
                return

            # 禁用按钮
            self._ai_analyze_btn.setEnabled(False)
            self._ai_analyze_btn.setText("分析中...")
            self._progress_bar.setValue(0)

            # 创建并启动工作线程
            self._analysis_worker = _AnalysisWorker(self._project)
            self._analysis_worker.finished.connect(self._on_analysis_finished)
            self._analysis_worker.error_occurred.connect(self._on_analysis_error)
            self._analysis_worker.start()

        except Exception as exc:
            logger.error("AI 分析启动失败: %s", exc)
            self._ai_analyze_btn.setEnabled(True)
            self._ai_analyze_btn.setText("AI 分析")
            self._show_status("分析启动失败: " + str(exc))

    def _on_analysis_finished(self, segments: list[models.Segment]) -> None:
        """AI 分析完成，在主线程中更新 UI。"""
        try:
            # 更新项目片段
            if self._project is not None:
                self._project.segments = segments

            # 更新 timeline
            self._timeline.set_project(self._project)

            # 更新片段列表
            self._update_segment_list()

            # 更新进度条
            self._progress_bar.setValue(100)

            # 启用按钮
            self._ai_analyze_btn.setEnabled(True)
            self._ai_analyze_btn.setText("AI 分析")

            self._show_status(
                f"AI 分析完成: {len(segments)} 个片段"
            )

        except Exception as exc:
            logger.error("分析结果处理失败: %s", exc)
            self._ai_analyze_btn.setEnabled(True)
            self._ai_analyze_btn.setText("AI 分析")

    def _on_analysis_error(self, error_msg: str) -> None:
        """AI 分析出错处理。"""
        try:
            # 启用按钮
            self._ai_analyze_btn.setEnabled(True)
            self._ai_analyze_btn.setText("AI 分析")
            
            # 重置进度条
            self._progress_bar.setValue(0)
            
            # 显示错误信息
            self._show_status(f"AI 分析失败: {error_msg}")
            
            logger.error("AI 分析失败: %s", error_msg)
        except Exception as exc:
            logger.error("分析错误处理失败: %s", exc)

    # ─── 信号槽 ──

    def _on_timeline_time_selected(self, time_sec: float) -> None:
        """时间线点击 → 播放器跳转。"""
        try:
            self._player.seek_to(time_sec)
        except Exception as exc:
            logger.debug("时间线跳转: %s", exc)

    def _on_segment_toggled(self, index: int, keep: bool) -> None:
        """片段切换 → 同步片段列表。"""
        try:
            if 0 <= index < self._segment_list.count():
                item: QListWidgetItem = self._segment_list.item(index)
                if item is not None and self._project is not None:
                    seg: models.Segment = self._project.segments[index]
                    seg.keep = keep
                    # 用 Segment 数据重建显示文本，而非在旧文本前追加
                    ts: str = f"{int(seg.start_time // 60):02d}:{int(seg.start_time % 60):02d}"
                    te: str = f"{int(seg.end_time // 60):02d}:{int(seg.end_time % 60):02d}"
                    checked: str = "☑" if keep else "☐"
                    item.setText(f"{checked} [{ts}-{te}] {seg.label}")
        except Exception as exc:
            logger.debug("片段切换同步: %s", exc)

    def _on_segment_list_changed(self, row: int) -> None:
        """片段列表变化 → 高亮时间线对应片段。"""
        try:
            if row >= 0 and self._timeline.project is not None:
                # 时间线会高亮对应片段
                self._timeline.update()
        except Exception as exc:
            logger.debug("列表变化处理: %s", exc)

    def _on_ai_settings(self) -> None:
        """处理 AI 设置: 显示当前配置信息。"""
        try:
            mock_text: str = "Mock" if config.ENABLE_CACHE else "API"
            msg: str = (
                f"AI 设置\n\n"
                f"模型: {config.OLLAMA_MODEL_NAME}\n"
                f"API: {config.OLLAMA_API_URL}\n"
                f"模式: {mock_text}\n"
                f"输出目录: {config.OUTPUT_DIR}"
            )
            self._show_status(msg)
        except Exception as exc:
            logger.error("AI 设置: %s", exc)

    # ─── UI 辅助 ──

    def _update_segment_list(self) -> None:
        """更新片段列表显示。"""
        try:
            self._segment_list.clear()

            if self._project is None:
                return

            for seg in self._project.segments:
                # 格式: [00:00-00:05] 片段标签
                start_str: str = _format_time(seg.start_time)
                end_str: str = _format_time(seg.end_time)
                label: str = seg.label

                # 状态标记
                status: str = "☑" if seg.keep else "☐"

                item_text: str = (
                    f"{status} [{start_str}-{end_str}] {label}"
                )

                item: QListWidgetItem = QListWidgetItem(item_text)
                item.setData(Qt.UserRole, seg)  # 存储 Segment 对象
                self._segment_list.addItem(item)

        except Exception as exc:
            logger.error("更新片段列表失败: %s", exc)

    def _update_status_bar(self) -> None:
        """更新状态栏信息。"""
        if self._project is None:
            return

        self._file_label.setText(f"文件: {Path(self._project.video_path).name}")
        self._duration_label.setText(f"时长: {_format_duration(self._project.duration)}")
        self._fps_label.setText(f"帧率: {self._project.fps:.0f}fps")
        self._resolution_label.setText(
            f"分辨率: {self._project.width}x{self._project.height}"
        )

    def _show_status(self, message: str) -> None:
        """显示临时状态消息。"""
        self.status_bar.showMessage(message, 3000)

    # ─── 事件处理 ──

    def closeEvent(self, event: Any) -> None:
        """关闭事件，确保资源正确释放。"""
        try:
            # 停止分析线程
            if (
                self._analysis_worker is not None
                and self._analysis_worker.isRunning()
            ):
                self._analysis_worker.terminate()

            self._show_status("程序已退出")
            event.accept()
        except Exception as exc:
            logger.debug("关闭事件: %s", exc)
            event.accept()


# ─── 入口 ──


def main() -> None:
    """主程序入口。"""
    # 初始化日志
    config.setup_logging()
    config.ensure_directories()

    # 创建应用
    app: QApplication = QApplication(sys.argv)

    # 设置中文字体
    font: QFont = QFont()
    font.setFamily("PingFang SC, Microsoft YaHei, sans-serif")
    font.setPointSize(11)
    app.setFont(font)

    # 创建并显示主窗口
    window: MainWindow = MainWindow()
    window.show()

    # 启动事件循环
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
