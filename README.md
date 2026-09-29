# 虾片

AI 口播精剪：上传毛片 → 理解画面和逐字稿 → 文字即时间线微调 → 导出成片。

适合知识口播 / 讲书 / 现场口播。对标 ChatCut 的「改字即改片」，不做剪映那种特效商城。

## 能做什么

- 多段毛片上传（大文件分片，躲开 Cloudflare 100MB 限制）
- Gemini 看视频 + 百炼字级 ASR 出粗剪
- 口播稿勾选删留，不必重跑 AI
- 精修：改字幕、美颜、滤镜、转场
- 口播说到「第一、第二」时自动出要点卡
- 试用登录（账号在本地 `users.json`，不要提交密钥）

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.10+ / FastAPI / Uvicorn |
| 前端 | React 18 / TypeScript / Vite |
| 视频 | FFmpeg |
| 理解 | Gemini（`gemini.hhhman.top` 或官方 API） |
| 语音 | 阿里云百炼 `qwen3-asr-flash-filetrans`（字级时间戳） |

## 本地运行

```bash
git clone https://github.com/hhhman-chen/xiapian.git
cd xiapian
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# 填 GEMINI_API_KEY、DASHSCOPE_API_KEY、AUTH_SECRET

cd web/frontend && npm install && npm run build && cd ../..
python3 -m web.backend.app
```

浏览器打开 http://127.0.0.1:8000

`.env` 示例见 `.env.example`。不要把真实 Key、`users.json`、成片和项目目录推到 GitHub。

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
