"""
Qwen3-ASR 服务（transformers 后端 + ForcedAligner）

- 模型：Qwen3-ASR-1.7B + Qwen3-ForcedAligner-0.6B（字符级时间戳）
- 端口 8123
- /transcribe 返回 segments，每个 segment 含 words（字符级时间戳）

启动：
  source ~/.openclaw/workspace/venv-qwen-asr/bin/activate
  HF_ENDPOINT=https://hf-mirror.com \
    python3 backend/services/qwen_asr_server.py
"""
import os
import re
import time
import logging
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("qwen_asr_server")

ASR_PORT = int(os.environ.get("ASR_PORT", "8123"))
ASR_HOST = os.environ.get("ASR_HOST", "0.0.0.0")

# 本地模型路径：优先 1.7B（已下载到 HF cache），降级 backend/models/Qwen3-ASR-0.6B
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 1.7B 在 HF cache
import glob as _glob
_asr_1_7b_candidates = _glob.glob(os.path.expanduser(
    "~/.cache/huggingface/hub/models--Qwen--Qwen3-ASR-1.7B/snapshots/*"
))
if _asr_1_7b_candidates and (
    os.path.exists(os.path.join(_asr_1_7b_candidates[0], "model.safetensors"))
    or os.path.exists(os.path.join(_asr_1_7b_candidates[0], "model.safetensors.index.json"))
):
    ASR_MODEL_PATH = os.environ.get("ASR_MODEL", _asr_1_7b_candidates[0])
else:
    ASR_MODEL_PATH = os.environ.get(
        "ASR_MODEL",
        os.path.join(BACKEND_DIR, "models", "Qwen", "Qwen3-ASR-0.6B"),
    )
ALIGNER_MODEL_PATH = os.environ.get(
    "ALIGNER_MODEL",
    os.path.join(BACKEND_DIR, "models", "Qwen", "Qwen3-ForcedAligner-0.6B"),
)

app = FastAPI(title="Qwen3-ASR Service", version="2.0.0")

_model = None


class TranscribeRequest(BaseModel):
    audio_path: str
    language: Optional[str] = "zh"
    hotwords: Optional[str] = ""


class TranscribeResponse(BaseModel):
    text: str
    language: str
    duration: float
    segments: list  # [{start, end, text, words: [{word, start, end}]}]
    model: str
    elapsed_s: float
    backend: str  # transformers-forced-aligner / transformers / mlx-fallback


@app.on_event("startup")
async def startup():
    global _model
    logger.info("=" * 60)
    logger.info("[qwen-asr] ASR model: %s", ASR_MODEL_PATH)
    logger.info("[qwen-asr] aligner:   %s", ALIGNER_MODEL_PATH)
    t0 = time.time()
    try:
        from qwen_asr import Qwen3ASRModel
        aligner_arg = ALIGNER_MODEL_PATH if os.path.exists(ALIGNER_MODEL_PATH) else None
        if aligner_arg:
            logger.info("[qwen-asr] loading with ForcedAligner")
        else:
            logger.warning("[qwen-asr] ForcedAligner model not found, loading ASR only")
        _model = Qwen3ASRModel.from_pretrained(
            ASR_MODEL_PATH,
            forced_aligner=aligner_arg,
        )
        # Mac 上用 MPS 加速（CUDA 不可用）
        import torch as _torch
        if _torch.cuda.is_available():
            _model.model.to("cuda")
            logger.info("[qwen-asr] on CUDA")
        elif _torch.backends.mps.is_available():
            try:
                _model.model.to("mps")
                logger.info("[qwen-asr] on MPS (Metal)")
            except Exception as e:
                logger.warning("[qwen-asr] MPS move failed, staying on CPU: %s", e)
        else:
            logger.info("[qwen-asr] on CPU")
        logger.info("[qwen-asr] loaded in %.1fs", time.time() - t0)
    except Exception as e:
        logger.exception("[qwen-asr] failed to load: %s", e)
        # 不 raise，让 health 反映未加载，后续降级到 mlx
        _model = None


@app.get("/")
async def root():
    return {"service": "Qwen3-ASR", "model": ASR_MODEL_PATH, "aligner": ALIGNER_MODEL_PATH, "loaded": _model is not None}


@app.get("/health")
async def health():
    if _model is None:
        raise HTTPException(status_code=503, detail="model not loaded")
    return {"status": "ok", "model": ASR_MODEL_PATH, "aligner": ALIGNER_MODEL_PATH}


@app.post("/transcribe", response_model=TranscribeResponse)
async def transcribe(req: TranscribeRequest):
    if _model is None:
        # 降级 mlx
        return await _transcribe_mlx_fallback(req)

    if not os.path.exists(req.audio_path):
        raise HTTPException(status_code=404, detail=f"audio not found: {req.audio_path}")

    t0 = time.time()
    # transformers 版用全名（Chinese），mlx 版用 zh
    lang_map = {"zh": "Chinese", "en": "English", "yue": "Cantonese",
                "ja": "Japanese", "ko": "Korean", "auto": None}
    language = lang_map.get(req.language, req.language) if req.language else None
    if req.language and req.language != "auto" and not language:
        language = req.language
    context = f"Vocabulary: {req.hotwords}." if req.hotwords else ""

    try:
        results = _model.transcribe(
            req.audio_path,
            language=language,
            context=context,
            return_time_stamps=True,
        )
    except Exception as e:
        logger.exception("[qwen-asr] transcribe failed: %s", e)
        return await _transcribe_mlx_fallback(req)

    elapsed = time.time() - t0

    # results 是 list（qwen_asr 返回 List[ASRTranscription]）
    if not results:
        raise HTTPException(status_code=500, detail="empty result")

    res = results[0]
    full_text = getattr(res, "text", "") or ""
    ts_list = getattr(res, "time_stamps", []) or []

    # 把字符级时间戳聚合成句子（按 full_text 里的标点切分）
    # time_stamps 不含标点，但 full_text 含；用"非标点字符位置"对齐
    segments = _aggregate_to_sentences_v2(ts_list, full_text)

    # 没有 timestamps 就退化为整段
    if not segments and full_text:
        segments = [{"start": 0.0, "end": 0.0, "text": full_text, "words": []}]

    return TranscribeResponse(
        text=full_text,
        language=req.language or "zh",
        duration=segments[-1]["end"] if segments else 0.0,
        segments=segments,
        model=os.path.basename(ASR_MODEL_PATH),
        elapsed_s=round(elapsed, 2),
        backend="transformers-forced-aligner" if ts_list else "transformers",
    )


# ─── mlx 降级 ──────────────────────────────────────────

async def _transcribe_mlx_fallback(req: TranscribeRequest) -> TranscribeResponse:
    """mlx 版降级（无字符级时间戳）"""
    global _model
    logger.warning("[qwen-asr] falling back to mlx-whisper")
    try:
        import mlx_whisper
        result = mlx_whisper.transcribe(
            req.audio_path,
            path_or_hf_repo="mlx-community/whisper-large-v3-turbo",
            language="zh",
            word_timestamps=True,
        )
        segments = []
        for seg in result.get("segments", []):
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            segments.append({
                "start": round(seg.get("start", 0), 3),
                "end": round(seg.get("end", 0), 3),
                "text": text,
                "words": [
                    {"word": w.get("word", ""),
                     "start": round(w.get("start", 0), 3),
                     "end": round(w.get("end", 0), 3)}
                    for w in seg.get("words", [])
                ],
            })
        return TranscribeResponse(
            text=result.get("text", ""),
            language="zh",
            duration=segments[-1]["end"] if segments else 0.0,
            segments=segments,
            model="mlx-whisper-large-v3-turbo",
            elapsed_s=0.0,
            backend="mlx-fallback",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"both transformers and mlx failed: {e}")


# ─── 字符级时间戳 → 句子聚合 ──────────────────────────

SENTENCE_END = re.compile(r"([。！？!?；;\n])")


def _aggregate_to_sentences_v2(ts_list: list, full_text: str) -> list:
    """
    ts_list: 字符级时间戳（不含标点）
    full_text: 完整文本（含标点）

    把 ts_list 的时间戳按 full_text 中的标点切分成句子。
    """
    if not ts_list or not full_text:
        return []

    import re
    CHAR_RE = re.compile(r"[a-zA-Z0-9\u4e00-\u9fff]")
    PUNCT_END = set("。！？!?；;\n")

    # 标准化 ts_list
    norm_ts = []
    for ts in ts_list:
        if hasattr(ts, "start_time"):
            norm_ts.append((ts.text, float(ts.start_time), float(ts.end_time)))
        else:
            norm_ts.append((ts.get("text", ""), float(ts.get("start_time", 0)), float(ts.get("end_time", 0))))

    segments = []
    cur_words = []
    cur_text = ""
    ts_idx = 0

    for ch in full_text:
        if CHAR_RE.match(ch):
            if ts_idx < len(norm_ts):
                _, s, e = norm_ts[ts_idx]
                cur_words.append({"word": ch, "start": round(s, 3), "end": round(e, 3)})
                cur_text += ch
                ts_idx += 1
            else:
                cur_text += ch
        else:
            cur_text += ch
            if ch in PUNCT_END and cur_words:
                segments.append({
                    "start": round(cur_words[0]["start"], 3),
                    "end": round(cur_words[-1]["end"], 3),
                    "text": cur_text.strip(),
                    "words": cur_words,
                })
                cur_words = []
                cur_text = ""

    if cur_words:
        segments.append({
            "start": round(cur_words[0]["start"], 3),
            "end": round(cur_words[-1]["end"], 3),
            "text": cur_text.strip(),
            "words": cur_words,
        })

    return [s for s in segments if s["text"]]


def _aggregate_to_sentences(ts_list: list) -> list:
    """
    ts_list: [{'start_time':..., 'end_time':..., 'text': '字'}, ...]（字符级）
    返回: [{'start', 'end', 'text', 'words': [{'word','start','end'}]}, ...]
    按句子结束标点切分，保留标点。
    """
    if not ts_list:
        return []

    segments = []
    cur_words = []
    cur_text = ""

    for ts in ts_list:
        # qwen_asr 的 time_stamps 元素可能是对象或 dict
        if hasattr(ts, "start_time"):
            char = ts.text
            s = ts.start_time
            e = ts.end_time
        else:
            char = ts.get("text", "")
            s = ts.get("start_time", 0)
            e = ts.get("end_time", 0)

        cur_words.append({"word": char, "start": round(float(s), 3), "end": round(float(e), 3)})
        cur_text += char

        # 句子结束
        if SENTENCE_END.match(char):
            segments.append({
                "start": round(cur_words[0]["start"], 3) if cur_words else 0,
                "end": round(cur_words[-1]["end"], 3) if cur_words else 0,
                "text": cur_text.strip(),
                "words": cur_words,
            })
            cur_words = []
            cur_text = ""

    # 末尾未结束的句子
    if cur_words:
        segments.append({
            "start": round(cur_words[0]["start"], 3),
            "end": round(cur_words[-1]["end"], 3),
            "text": cur_text.strip(),
            "words": cur_words,
        })

    # 过滤空段
    return [s for s in segments if s["text"]]


if __name__ == "__main__":
    import uvicorn
    logger.info("starting Qwen3-ASR server on %s:%d", ASR_HOST, ASR_PORT)
    uvicorn.run(app, host=ASR_HOST, port=ASR_PORT, log_level="info")