# 智能剪辑 Web UI

基于 FastAPI + React + Vite 的 AI 视频剪辑工具。

> 原 PyQt5 桌面版已归档到 `desktop/` 目录。

## 功能

- 上传视频，自动提取元数据
- **v8 默认 DeepSeek V4.1-Flash 一步剪**：抽帧 + ASR → 一次 `deepseek-flash` 看图读稿并直接给出 retain/delete（官方不收整段视频）。旧两阶段：`ANALYZE_BACKEND=kimi`
- 旧版 Ollama 分析仍保留（含 mock 模式）
- HTML5 视频播放器预览
- Canvas 时间线：拖拽片段边界、点击切换保留/删除
- 片段列表编辑标签与保留状态
- 导出剪辑后的 MP4 视频，可选逐字字幕烧录
- 项目 JSON 保存/加载

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.9+ / FastAPI / Uvicorn |
| 前端 | React 18 / TypeScript / Vite |
| 视频处理 | OpenCV / MoviePy 2.x / FFmpeg |
| AI 分析 | Moonshot K3 多模态 + Qwen3-ASR |
| 旧版 AI 分析 | Ollama API（支持 mock 模式） |

## 快速开始

### 1. 安装依赖

```bash
cd /Users/chenkangye/workspace/migrated/video-editor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

前端依赖已包含在 `web/frontend/node_modules`，如需重新安装：

```bash
cd web/frontend
npm install
npm run build
```

### 2. 启动 ASR 服务（声音通道）

v6 分析需要本地 Qwen3-ASR 服务做语音转录：

```bash
./start_asr.sh
```

它会在 `http://127.0.0.1:8123` 启动 Qwen3-ASR-1.7B + ForcedAligner。

### 3. 启动主服务

```bash
./start.sh
```

或手动启动后端：

```bash
source .venv/bin/activate
python3 -m web.backend.app
```

然后浏览器打开 http://127.0.0.1:8000

命令行验收（不走 UI）：

```bash
python3 -m web.backend.run_cut \
  --video ~/.openclaw/workspace/obsidian-vault-video-editor/赵湄原片.mp4 \
  --target 153 --export --subtitle
```

成片写到 `output/<文件名>_cut/final.mp4`。目标时长填 `0` 表示不强制二轮精简。

### 4. AI 模型配置

v6 分析默认调用 Moonshot K3 多模态模型，需要 API key：

- 环境变量：`MOONSHOT_API_KEY`
- 或文件：`~/.openclaw/workspace/moonshot_key.txt`

ASR 服务地址可改：

```bash
ASR_URL=http://127.0.0.1:8123 ./start.sh
```

如果 ASR 服务没起，v6 会降级为只走视觉通道。

旧版 Ollama 分析仍保留在 `/api/projects/{id}/analyze`，可设置：

```bash
OLLAMA_MODEL_NAME=kimi-k3:cloud OLLAMA_API_URL=http://127.0.0.1:11434 ./start.sh
MOCK_AI=true ./start.sh   # mock 模式
```

## 目录结构

```
video-editor/
├── web/
│   ├── backend/          # FastAPI 后端
│   │   ├── app.py
│   │   ├── dependencies.py
│   │   ├── engine_v6.py   # v6 K3 多模态剪辑引擎
│   │   ├── platforms.py   # 平台偏好配置
│   │   └── routers/
│   │       ├── projects.py
│   │       ├── videos.py
│   │       ├── segments.py
│   │       ├── export.py    # 旧 Ollama 分析/导出
│   │       ├── v6_export.py # v6 分析/导出
│   │       └── platforms.py
│   └── frontend/         # React 前端
│       ├── src/
│       │   ├── App.tsx
│       │   ├── api.ts
│       │   └── components/
│       └── dist/         # 构建产物
├── editor_core.py        # 视频处理核心（复用）
├── ai_analyzer.py        # AI 分析（复用）
├── models.py             # 数据模型（复用）
├── config.py             # 配置（复用）
├── desktop/              # 原 PyQt5 桌面版代码
├── start.sh              # 启动主服务
├── start_asr.sh          # 启动 Qwen3-ASR 服务
└── requirements.txt
```

## 开发模式

前后端分离开发：

```bash
# 终端 1：后端
source .venv/bin/activate
python3 -m web.backend.app

# 终端 2：前端 dev server
cd web/frontend
npm run dev
```

前端 dev server 运行在 http://127.0.0.1:5173，并代理 `/api` 到后端 8000。

## API 接口

| 接口 | 说明 |
|---|---|
| `POST /api/projects/upload` | 上传视频创建项目 |
| `GET /api/projects/{id}` | 获取项目 JSON |
| `POST /api/projects/{id}` | 保存项目 JSON |
| `GET /api/projects/{id}/video` | 视频流播放 |
| `GET /api/projects/{id}/frame?t=...` | 帧预览图 |
| `GET /api/platforms` | 获取平台列表 |
| `POST /api/projects/{id}/v6/analyze` | v6 K3 多模态 AI 分析 |
| `GET /api/projects/{id}/v6/analyze/status` | v6 分析状态 |
| `POST /api/projects/{id}/v6/export` | v6 导出视频 |
| `GET /api/projects/{id}/v6/export/status` | v6 导出状态 |
| `POST /api/projects/{id}/analyze` | 旧 Ollama AI 分析 |
| `GET /api/projects/{id}/analyze/status` | 旧分析状态 |
| `POST /api/projects/{id}/export` | 旧导出视频 |
| `GET /api/projects/{id}/export/status` | 旧导出状态 |
| `GET /api/projects/{id}/download` | 下载导出视频 |
