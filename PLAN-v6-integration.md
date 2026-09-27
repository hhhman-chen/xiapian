# v6 K3 多模态 engine 接入 Web 后端计划

## 目标
把 `/Users/chenkangye/.openclaw/workspace/video-editor-web/backend/engine.py` 的 v6 两阶段架构（视觉+声音理解 → K3 综合决策）接入 `/Users/chenkangye/workspace/migrated/video-editor/`，替换当前简单的 Ollama 分析器。

## 现状
- 当前项目：`/Users/chenkangye/workspace/migrated/video-editor/`
  - FastAPI 后端 + React + Vite 前端
  - 分析接口复用 `ai_analyzer.py`（采样 10 帧调 Ollama）
  - 导出接口复用 `editor_core.py`（moviepy subclip 拼接）
- v6 成熟项目：`/Users/chenkangye/.openclaw/workspace/video-editor-web/backend/`
  - engine.py：K3 多模态看视频 + Qwen3-ASR 听声音 + K3 决策
  - platforms.py：5 个平台偏好
  - 路由：analyze/export/platforms
  - 依赖：K3 API key、本地 Qwen3-ASR 服务（端口 8123）、ffmpeg-full

## 接入方案

### 1. 后端迁移
- 在 `web/backend/` 下新增：
  - `engine.py`（从 v6 项目复制并适配路径/配置）
  - `platforms.py`（复制）
  - `routers/platforms.py`（复制）
- 改造现有路由：
  - `routers/export.py`：改同步导出，接收 `{segments, transcript, with_subtitle}`，调用 v6 engine.export + 字幕生成
  - `routers/projects.py` 或新增 `routers/analyze.py`：接收 `{user_prompt, platform}`，调用 v6 engine.analyze
  - `routers/videos.py` 等保留
- 配置：复用 `config.py`，增加 K3 API key、ASR URL、ffmpeg-full 路径读取

### 2. 前端适配
- `api.ts`：新增/修改 analyze/export API，传递 `user_prompt`、`platform`、`with_subtitle`
- `App.tsx`：
  - 加平台选择下拉框
  - 加用户意图输入框
  - 加"生成字幕"开关
  - 接收 v6 返回的 suggestions/transcript/visual/summary，并转成当前 segments 模型
- `SegmentList/Timeline`：兼容 `action: retain|delete` 字段
- 导出时把 transcript 传给后端生成逐字字幕 SRT

### 3. ASR 服务
- 复用 `/Users/chenkangye/.openclaw/workspace/video-editor-desktop/backend/services/qwen_asr_server.py`
- 启动脚本：`start_asr.sh`（放在当前项目根目录）
- 依赖 venv：`~/.openclaw/workspace/venv-qwen-asr`

### 4. 依赖更新
- `requirements.txt`：增加 `httpx`、`openai`（K3 SDK）
- 保持 opencv/moviepy 等不变

### 5. 启动脚本
- `start.sh`：先检测/启动 ASR 服务，再启动 FastAPI 后端
- 或提供 `start_asr.sh` + `start.sh` 分开启动

## 接口调整

| 接口 | 当前 | 改造后 |
|---|---|---|
| `POST /api/projects/{id}/analyze` | 仅调用 Ollama | 改为 v6 两阶段分析，接收 `{user_prompt, platform}` |
| `POST /api/projects/{id}/export` | 导出当前 segments | 接收 `{segments, transcript, with_subtitle}` |
| `GET /api/platforms` | 无 | 新增，返回平台列表 |
| `GET /api/projects/{id}/download` | 已存在 | 保持 |

## 风险与处理
1. **ASR 服务未启动**：engine.analyze 降级为无 transcript 模式，K3 靠视觉决策
2. **K3 视觉失败**：降级为纯 ASR/声音决策
3. **K3 JSON 解析失败**：v6 已有 regex 降级提取
4. **ffmpeg-full 未安装**：字幕烧录降级为 mov_text 软字幕或无字幕

## 实施步骤
1. 复制 engine.py、platforms.py 到当前项目并适配
2. 新增/改造 analyze、export、platforms 路由
3. 更新 config.py 读取 K3 API key
4. 更新前端 API 和 UI
5. 新增 ASR 启动脚本
6. 更新 requirements.txt
7. 跑通端到端测试
