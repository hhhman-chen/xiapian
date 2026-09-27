# 智能剪辑项目 Web UI 改造计划

## 目标
将 `/Users/chenkangye/workspace/migrated/video-editor` 从 PyQt5 桌面版改造为 Web UI 版，保留现有的视频分析、片段剪辑、导出等核心能力。

## 当前项目分析
- `main.py` + `player.py` + `timeline_widget.py`：PyQt5 桌面 UI，约 2150 行，需要整体替换
- `editor_core.py`：视频加载/预览/裁剪/导出核心逻辑，可复用
- `ai_analyzer.py`：Ollama API 视频分析，可复用
- `models.py`：Project / Segment 数据模型，可复用
- `config.py`：配置模块，可复用

## 推荐方案：FastAPI 后端 + React + Vite 前端

### 架构
```
video-editor/
├── web/
│   ├── backend/
│   │   ├── app.py              # FastAPI 入口
│   │   ├── routers/
│   │   │   ├── projects.py     # 项目 CRUD
│   │   │   ├── videos.py       # 视频上传/流式播放/帧预览
│   │   │   ├── segments.py     # 片段增删改查
│   │   │   └── export.py       # AI 分析与视频导出
│   │   └── dependencies.py     # Project 加载/持久化辅助
│   └── frontend/
│       ├── index.html
│       ├── package.json
│       ├── vite.config.ts
│       ├── src/
│       │   ├── main.tsx        # React 入口
│       │   ├── App.tsx         # 主布局
│       │   ├── api.ts          # 后端接口封装
│       │   ├── components/
│       │   │   ├── VideoPlayer.tsx      # HTML5 视频播放器
│       │   │   ├── Timeline.tsx         # Canvas 时间线
│       │   │   ├── SegmentList.tsx      # 片段列表
│       │   │   ├── Toolbar.tsx          # AI 分析/导出/保存按钮
│       │   │   └── ProjectInfo.tsx      # 视频元信息
│       │   └── types.ts        # 前端类型
├── editor_core.py             # 复用：视频处理核心
├── ai_analyzer.py             # 复用：AI 分析
├── models.py                  # 复用：数据模型
├── config.py                  # 复用：配置
├── requirements.txt           # 更新：新增 fastapi/uvicorn 等
└── desktop/                   # 保留原桌面代码
    └── README.md
```

### 后端接口
| 接口 | 说明 |
|------|------|
| `POST /api/projects/upload` | 上传视频，创建项目 |
| `GET /api/projects/{id}` | 获取项目 JSON |
| `POST /api/projects/{id}` | 保存项目 JSON |
| `GET /api/projects/{id}/video` | 视频流式播放（range 支持） |
| `GET /api/projects/{id}/frame?t=...` | 指定时间点帧预览 |
| `POST /api/projects/{id}/analyze` | AI 分析，返回 segments |
| `POST /api/projects/{id}/export` | 导出剪辑后的视频 |
| `GET /api/projects/{id}/export/status` | 查询导出进度 |
| `GET /api/projects/{id}/download` | 下载导出结果 |

### 前端页面
1. **上传区**：拖拽/选择视频文件
2. **播放器区**：HTML5 `<video>` + 播放/暂停/进度/跳转
3. **时间线区**：Canvas 绘制 segments，支持点击切换 keep/drop、拖拽边界调整起止时间
4. **片段列表区**：列表展示每个 segment，可编辑标签、切换保留状态
5. **工具栏**：AI 分析、保存项目、导出视频、显示进度

### 保留的桌面版能力
- 视频加载元数据（fps/width/height/duration）
- AI 自动分析精彩片段 / 静音段 / 转场段
- 片段保留/删除切换、边界拖拽调整
- 按保留片段拼接导出 MP4
- 项目 JSON 保存/加载

### 新增/调整
- 用 FastAPI 替代 PyQt5 事件循环
- 用 WebSocket 或 Server-Sent Events 推送 AI 分析和导出进度
- 用 HTML5 video + Canvas 替代 Qt player/timeline
- 原 `main.py` / `player.py` / `timeline_widget.py` 移动到 `desktop/` 保留备用

## 备选方案
如果不希望引入 React 构建流程，可改用 **FastAPI + 原生 HTML/JS 单页**：
- 优点：更轻量，少一个构建步骤，维护简单
- 缺点：组件化、状态管理、复杂交互不如 React 方便
- 推荐仍用 React，因为你其他项目（SmartCare）也是 React + Vite，栈统一。

## 实施步骤
1. 整理目录，把桌面代码移入 `desktop/`
2. 更新 `requirements.txt`，添加 `fastapi`, `uvicorn[standard]`, `python-multipart`, `aiofiles`
3. 搭建 FastAPI 后端与项目持久化（内存 + JSON 文件）
4. 搭建 React + Vite 前端，实现上传与视频播放器
5. 实现 Canvas 时间线组件（复现原 timeline_widget 交互）
6. 实现片段列表与 AI 分析/导出/保存接口对接
7. 跑通端到端流程并测试
