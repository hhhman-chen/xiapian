"""
视频剪辑引擎 v8：默认 DeepSeek V4.1-Flash 一步理解和决策。

官方 deepseek-flash 只收图片不收视频，所以：
  ASR（切点） + 均匀抽帧 → 一次 deepseek-flash 调用（看图+读稿）→ retain/delete

旧两阶段（K2.6 视觉 + K3 决策）仍可用：ANALYZE_BACKEND=kimi
"""
import os
import json
import time
import logging
import asyncio
import subprocess
from typing import Dict, Any, List, Optional

import inspect
import httpx
from openai import OpenAI


def _async_httpx(**kwargs) -> httpx.AsyncClient:
    """兼容 httpx 0.25 (proxies=) 和 0.28+ (proxy=)。"""
    proxy = kwargs.pop("proxy", None)
    if proxy:
        params = inspect.signature(httpx.AsyncClient.__init__).parameters
        if "proxy" in params:
            kwargs["proxy"] = proxy
        else:
            kwargs["proxies"] = proxy
    return httpx.AsyncClient(**kwargs)

logger = logging.getLogger("engine")

# ─── 配置 ──────────────────────────────────────────
MOONSHOT_BASE_URL = "https://api.moonshot.cn/v1"
K3_MODEL = "kimi-k3"
# K2.8 没有独立 API ID；理解层用更快的 k2.6，决策层用 K3 high（不要默认 max）
VISUAL_MODEL = os.environ.get("VISUAL_MODEL", "kimi-k2.6")
DECISION_MODEL = os.environ.get("DECISION_MODEL", "kimi-k3")
VISUAL_EFFORT = os.environ.get("VISUAL_EFFORT", "low")
DECISION_EFFORT = os.environ.get("DECISION_EFFORT", "high")
ROUND2_EFFORT = os.environ.get("ROUND2_EFFORT", "low")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
GEMINI_BASE_URL = os.environ.get("GEMINI_BASE_URL", "https://gemini.hhhman.top").rstrip("/")
# 付费档优先 3.8 整段 agentic；失败再 3.7 / 2.5
GEMINI_FALLBACK_MODELS = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-2.5-flash"]
ASR_HOTWORDS = os.environ.get(
    "ASR_HOTWORDS",
    "哈利波特,斯内普,死亡圣器,伏地魔,邓布利多,赵湄,罗恩,赫敏,马尔福,海格,麦格,西弗勒斯,莉莉",
)
# 成片超过目标的这个比例才触发二轮精简（仅 kimi 两阶段）
ROUND2_OVERSHOOT = float(os.environ.get("ROUND2_OVERSHOOT", "1.12"))
ANALYZE_BACKEND = os.environ.get("ANALYZE_BACKEND", "gemini").strip().lower()
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")
DEEPSEEK_EFFORT = os.environ.get("DEEPSEEK_EFFORT", "low")
DEEPSEEK_FRAMES = int(os.environ.get("DEEPSEEK_FRAMES", "28"))

# ASR：cloud=百炼字级时间戳；local=本机 Qwen3-ASR；auto=有百炼 key 就走云
ASR_URL = os.environ.get("ASR_URL", "http://localhost:8123")
ASR_BACKEND = os.environ.get("ASR_BACKEND", "auto").strip().lower()
BAILIAN_BASE_URL = os.environ.get("BAILIAN_BASE_URL", "https://dashscope.aliyuncs.com")
BAILIAN_ASR_MODEL = os.environ.get("BAILIAN_ASR_MODEL", "qwen3-asr-flash-filetrans")

# ffmpeg-full（带 libass，烧字幕）
_FFMPEG_FULL = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
import shutil


def _resolve_bin(name: str, extras=None) -> str:
    found = shutil.which(name)
    if found:
        return found
    for c in extras or []:
        if c and os.path.exists(c):
            return c
    return name


FFMPEG = _resolve_bin("ffmpeg", [_FFMPEG_FULL, "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"])
FFPROBE = _resolve_bin("ffprobe", [
    "/opt/homebrew/opt/ffmpeg-full/bin/ffprobe",
    "/opt/homebrew/bin/ffprobe",
    "/usr/local/bin/ffprobe",
])

# 平台偏好
from web.backend.platforms import get_profile


# 延迟初始化 K3 client，避免导入时因 key 不存在报错
_k3_client: Optional[OpenAI] = None


def _read_moonshot_key() -> str:
    """安全读取 Moonshot API key。"""
    env_key = os.environ.get("MOONSHOT_API_KEY", "")
    if env_key:
        return env_key
    key_file = os.path.expanduser("~/.openclaw/workspace/moonshot_key.txt")
    if os.path.exists(key_file):
        try:
            return open(key_file).read().strip()
        except Exception:
            pass
    return ""


def get_k3_client() -> OpenAI:
    global _k3_client
    if _k3_client is None:
        key = _read_moonshot_key()
        if not key:
            raise RuntimeError("缺少 MOONSHOT_API_KEY，请设置环境变量或 ~/.openclaw/workspace/moonshot_key.txt")
        _k3_client = OpenAI(api_key=key, base_url=MOONSHOT_BASE_URL)
    return _k3_client


def _read_gemini_key() -> str:
    env_key = os.environ.get("GEMINI_API_KEY", "")
    if env_key:
        return env_key
    for path in (
        os.path.expanduser("~/.openclaw/workspace/gemini_key.txt"),
        os.path.expanduser("~/.openclaw/workspace/google_gemini_key.txt"),
    ):
        if os.path.exists(path):
            try:
                return open(path).read().strip()
            except Exception:
                pass
    return ""


def _gemini_proxy() -> Optional[str]:
    """Gemini 走系统/Clash 代理；国内直连经常超时。Moonshot 不要走这个。"""
    return os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or "http://127.0.0.1:17890"


def _read_dashscope_key() -> str:
    for env_name in ("DASHSCOPE_API_KEY", "BAILIAN_API_KEY"):
        val = os.environ.get(env_name, "")
        if val and "…" not in val and "..." not in val:
            return val
    for path in (
        os.path.expanduser("~/.openclaw/workspace/bailian_key.txt"),
        os.path.expanduser("~/video-editor/bailian_key.txt"),
    ):
        if os.path.exists(path):
            try:
                val = open(path, encoding="utf-8-sig").read().strip()
                if val and "…" not in val:
                    return val
            except Exception:
                pass
    return ""


def asr_uses_cloud() -> bool:
    if ASR_BACKEND in ("cloud", "bailian", "dashscope"):
        return True
    if ASR_BACKEND == "local":
        return False
    return bool(_read_dashscope_key())


def _read_deepseek_key() -> str:
    env_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if env_key and "…" not in env_key and "..." not in env_key:
        return env_key
    env_file = os.path.expanduser("~/.openclaw/service-env/ai.openclaw.gateway.env")
    if os.path.exists(env_file):
        try:
            for line in open(env_file):
                if line.strip().startswith("DEEPSEEK_API_KEY="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val and "…" not in val:
                        return val
        except Exception:
            pass
    cfg = os.path.expanduser("~/.openclaw/openclaw.json")
    if os.path.exists(cfg):
        try:
            data = json.load(open(cfg))
            val = (
                data.get("models", {})
                .get("providers", {})
                .get("deepseek", {})
                .get("apiKey", "")
            )
            if val and "…" not in val:
                return val
        except Exception:
            pass
    return ""


# ─── 主流程 ──────────────────────────────────────────

async def analyze(video_path: str, user_prompt: str = "", custom_prompt: Optional[str] = None,
                   platform: str = "general", target_duration: Optional[float] = None) -> Dict[str, Any]:
    """默认 Gemini agentic 看片 + 百炼字级切点。ANALYZE_BACKEND=deepseek|kimi 走旧路径。"""
    backend = ANALYZE_BACKEND
    if backend in ("deepseek", "ds-flash", "deepseek-flash", "v4.1", "v41", "flash"):
        return await _analyze_deepseek_oneshot(
            video_path, user_prompt, custom_prompt, platform, target_duration
        )
    if backend in ("kimi", "k3", "two-stage"):
        return await _analyze_kimi_two_stage(
            video_path, user_prompt, custom_prompt, platform, target_duration
        )
    return await _analyze_gemini_cut(
        video_path, user_prompt, custom_prompt, platform, target_duration
    )


async def _analyze_gemini_cut(
    video_path: str, user_prompt: str, custom_prompt: Optional[str],
    platform: str, target_duration: Optional[float],
) -> Dict[str, Any]:
    """Gemini 只给语义（看什么、删什么），切点全部来自字级 ASR。"""
    t0 = time.time()
    meta = _get_video_meta(video_path)
    duration = meta["duration"]
    profile = get_profile(platform)
    default_prompt = "去掉口头禅、停顿、导师插话、口误和重复，保留完整叙事和金句"
    final_prompt = custom_prompt or user_prompt or default_prompt
    logger.info("[engine] gemini-cut: %.1fs %dx%d target=%s", duration, meta["width"], meta["height"], target_duration)

    visual_task = _visual_understanding(video_path, duration, meta)
    audio_task = _audio_understanding(video_path, duration)
    visual, audio = await asyncio.gather(visual_task, audio_task, return_exceptions=True)

    if isinstance(visual, Exception):
        logger.warning("[engine] visual failed: %s", visual)
        visual = {"success": False, "backend": "failed", "visual_summary": str(visual)[:120]}
    if isinstance(audio, Exception):
        logger.warning("[engine] asr failed: %s", audio)
        audio = {"transcript": [], "speech_analysis": {}, "success": False}

    prep_s = round(time.time() - t0, 1)
    transcript = [dict(s) for s in audio.get("transcript", [])]
    t1 = time.time()
    transcript, cut_meta = _assemble_cut(
        transcript, visual, audio.get("speech_analysis") or {},
        target_duration, final_prompt, duration,
    )
    assemble_s = round(time.time() - t1, 1)
    segments = _sentences_to_segments(transcript, duration) if transcript else _normalize_segments([], duration)
    keep_segs = [s for s in segments if s["action"] == "retain"]
    keep_dur = sum(s["time_range"][1] - s["time_range"][0] for s in keep_segs)

    result = {
        "summary": visual.get("visual_summary") or cut_meta.get("summary", ""),
        "video_type": visual.get("video_type") or "口播",
        "video_style": visual.get("video_style") or "",
        "platform_applied": platform,
        "platform_label": profile["label"],
        "statistics": {
            "total_duration": round(duration, 2),
            "retained_duration": round(keep_dur, 2),
            "deleted_duration": round(duration - keep_dur, 2),
            "total_segments": len(segments),
            "retained_segments": len(keep_segs),
            "round1_duration": round(cut_meta.get("before_budget_s") or keep_dur, 2),
            "target_duration": target_duration,
            "round2_applied": bool(cut_meta.get("budget_applied")),
        },
        "suggestions": segments,
        "transcript": transcript,
        "visual_understanding": visual,
        "decision_reasoning": cut_meta.get("reasoning", ""),
        "round2": cut_meta.get("budget") or None,
        "timings": {"prep_s": prep_s, "assemble_s": assemble_s, "total_s": round(time.time() - t0, 1)},
        "model": {
            "visual": visual.get("backend") or GEMINI_MODEL,
            "decision": "asr-word-timeline",
            "cut": "gemini-semantic + word-recut + duration-budget",
        },
        "mock": False,
        "elapsed_s": round(time.time() - t0, 1),
    }
    pack = _pack_from_transcript(transcript)
    result["pack"] = pack
    logger.info("[engine] DONE gemini-cut: %.1fs → %.1fs (%d retain) total %.1fs",
                duration, keep_dur, len(keep_segs), result["elapsed_s"])
    return result


def _pack_from_transcript(transcript: List[Dict]) -> Dict[str, Any]:
    """从逐字稿抽「第一/第二…」章节，新素材也能出要点卡，不写死某一条片。"""
    import re
    chapters = []
    for s in transcript:
        if not s.get("keep", True):
            continue
        text = str(s.get("text") or "").strip()
        m = re.search(r"第([一二三四五六七八九十\d]+)[、，,.．]?\s*(.*)", text)
        if not m:
            continue
        rest = (m.group(2) or "").strip("，。,. ")
        title = rest[:16] if rest else text[:16]
        chapters.append({
            "start": round(float(s.get("start", 0)), 2),
            "end": round(float(s.get("end", 0)), 2),
            "index": "第" + m.group(1),
            "title": title,
            "layout": "card",
        })
    return {"chapters": chapters, "style": "card-points"}


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _assemble_cut(
    transcript: List[Dict], visual: Dict, speech: Dict,
    target: Optional[float], user_prompt: str, duration: float,
) -> tuple:
    """语义来自 Gemini，边界来自字级 ASR。返回 (transcript, meta)。"""
    if not transcript:
        return transcript, {"reasoning": "无逐字稿", "summary": visual.get("visual_summary", "")}

    def _is_protected(s: Dict) -> bool:
        text = s.get("text") or ""
        if any(k in text for k in ("真正的强大", "没有恨", "选择爱", "我是赵湄", "王子的故事")):
            return True
        a0, a1 = float(s.get("start", 0)), float(s.get("end", 0))
        if a0 < 15:
            return True
        if duration > 0 and a1 >= duration - 30:
            return True
        return False

    for s in transcript:
        s["keep"] = True
        s["reason"] = s.get("reason") or "默认保留"

    # 1) 口癖整句、高度重复句
    filler_idx = {x["idx"] for x in speech.get("fillers") or []}
    repeat_idx = {x["idx"] for x in speech.get("repeats") or []}
    for i, s in enumerate(transcript):
        if _is_protected(s):
            continue
        if i in filler_idx:
            s["keep"] = False
            s["reason"] = "口癖/填充句"
        elif i in repeat_idx:
            s["keep"] = False
            s["reason"] = "与上一句高度重复"

    # 2) Gemini 建议删除的时间范围（重叠过半则删）
    deletes = visual.get("delete_recommendations") or []
    for rec in deletes:
        try:
            d0, d1 = float(rec.get("start", 0)), float(rec.get("end", 0))
        except (TypeError, ValueError):
            continue
        if d1 <= d0:
            continue
        reason = rec.get("reason") or rec.get("desc") or "视觉建议删除"
        for s in transcript:
            if not s.get("keep"):
                continue
            a0, a1 = float(s.get("start", 0)), float(s.get("end", 0))
            if a1 <= a0:
                continue
            if _is_protected(s):
                continue
            if _overlap(a0, a1, d0, d1) >= 0.5 * (a1 - a0):
                s["keep"] = False
                s["reason"] = "Gemini: " + str(reason)[:80]

    # 3) 字级再切：句内 NG / 句首尾口癖字
    ng_n = 0
    for s in transcript:
        if s.get("keep") and _word_level_recut_sentence(s):
            ng_n += 1

    before = sum(float(s.get("end", 0)) - float(s.get("start", 0)) for s in transcript if s.get("keep"))
    budget_info = None
    budget_applied = False
    if target and target > 0 and before > target * ROUND2_OVERSHOOT:
        transcript, budget_info = _duration_budget(transcript, float(target), visual, duration)
        budget_applied = bool(budget_info and budget_info.get("dropped"))

    after = sum(float(s.get("end", 0)) - float(s.get("start", 0)) for s in transcript if s.get("keep"))
    reasoning = (
        f"切点来自字级 ASR；Gemini 删区 {len(deletes)} 条；字级 NG {ng_n} 句；"
        f"成片 {before:.0f}s → {after:.0f}s（目标 {target}）。{user_prompt[:80]}"
    )
    return transcript, {
        "reasoning": reasoning,
        "summary": visual.get("visual_summary", ""),
        "before_budget_s": round(before, 2),
        "budget_applied": budget_applied,
        "budget": budget_info,
    }


def _word_level_recut_sentence(sent: Dict) -> bool:
    """句内去掉首尾口癖字；长句里重复开场词只留最后一版。改 start/end/text/words。"""
    words = list(sent.get("words") or [])
    if not words:
        return False
    filler = set("呃额嗯啊哦噢唔呢吧嘛")
    changed = False

    def _is_filler(w):
        t = (w.get("word") or "").strip()
        return t in filler or t in {"那个", "就是", "然后"}

    while words and _is_filler(words[0]):
        words.pop(0)
        changed = True
    while words and _is_filler(words[-1]):
        words.pop()
        changed = True

    chars = [(w.get("word") or "") for w in words]
    text = "".join(chars)
    n = 6
    if len(text) >= n * 2:
        last = {}
        last_repeat_at = -1
        ci = 0
        index_at_char = []
        for wi, chs in enumerate(chars):
            for _ in chs:
                index_at_char.append(wi)
            ci += len(chs)
        for i in range(len(text) - n + 1):
            ng = text[i:i + n]
            if ng in last and i - last[ng] >= n:
                last_repeat_at = max(last_repeat_at, i)
            last[ng] = i
        if last_repeat_at > 0 and last_repeat_at < len(index_at_char):
            keep_from = index_at_char[last_repeat_at]
            if keep_from > 0 and keep_from < len(words) - 3:
                words = words[keep_from:]
                changed = True

    if not words:
        sent["keep"] = False
        sent["reason"] = (sent.get("reason") or "") + " | 字级切空"
        return True
    if changed:
        sent["words"] = words
        sent["start"] = float(words[0].get("start", sent.get("start", 0)))
        sent["end"] = float(words[-1].get("end", sent.get("end", 0)))
        sent["text"] = "".join((w.get("word") or "") for w in words)
        sent["reason"] = (sent.get("reason") or "") + " | 字级再切"
    return changed


def _duration_budget(transcript: List[Dict], target: float, visual: Dict, duration: float) -> tuple:
    """超过目标则按分数丢掉非保护句。保护开场/金句/收尾。"""
    protected = []
    for m in visual.get("key_moments") or []:
        try:
            protected.append((float(m.get("start", 0)), float(m.get("end", 0)), str(m.get("type") or "")))
        except (TypeError, ValueError):
            continue
    if duration > 0:
        protected.append((0.0, min(20.0, duration * 0.08), "hook"))
        protected.append((max(0.0, duration - 25.0), duration, "ending"))

    def is_protected(s):
        a0, a1 = float(s.get("start", 0)), float(s.get("end", 0))
        for b0, b1, _ in protected:
            if _overlap(a0, a1, b0, b1) >= 0.4 * max(0.01, a1 - a0):
                return True
        text = s.get("text") or ""
        if any(k in text for k in ("真正的强大", "没有恨", "选择爱", "我是赵湄")):
            return True
        return False

    kept = [s for s in transcript if s.get("keep")]
    cur = sum(float(s.get("end", 0)) - float(s.get("start", 0)) for s in kept)
    if cur <= target * ROUND2_OVERSHOOT:
        return transcript, {"dropped": 0, "before_s": round(cur, 1)}

    scored = []
    for s in kept:
        dur = float(s.get("end", 0)) - float(s.get("start", 0))
        text = s.get("text") or ""
        score = min(dur, 8.0) + min(len(text), 40) * 0.05
        if is_protected(s):
            score += 1000
        scored.append((score, s))
    scored.sort(key=lambda x: x[0])  # 低分先扔
    dropped = 0
    for score, s in scored:
        if cur <= target * ROUND2_OVERSHOOT:
            break
        if score >= 900:
            continue
        s["keep"] = False
        s["reason"] = (s.get("reason") or "") + " | 超时精简"
        cur -= float(s.get("end", 0)) - float(s.get("start", 0))
        dropped += 1
    return transcript, {"dropped": dropped, "before_s": round(cur + sum(
        float(s.get("end", 0)) - float(s.get("start", 0)) for _, s in scored if not s.get("keep")
    ), 1), "after_s": round(cur, 1)}


async def _analyze_deepseek_oneshot(
    video_path: str, user_prompt: str, custom_prompt: Optional[str],
    platform: str, target_duration: Optional[float],
) -> Dict[str, Any]:
    t0 = time.time()
    meta = _get_video_meta(video_path)
    duration = meta["duration"]
    profile = get_profile(platform)
    default_prompt = "去掉所有口头禅、停顿、重复内容和废话，保留有效内容，剪成一个流畅的视频"
    final_prompt = custom_prompt or user_prompt or default_prompt
    logger.info("[engine] deepseek-oneshot: %.1fs %dx%d platform=%s", duration, meta["width"], meta["height"], platform)

    audio_task = _audio_understanding(video_path, duration)
    frames_task = _extract_still_frames(video_path, duration, DEEPSEEK_FRAMES)
    audio, frames = await asyncio.gather(audio_task, frames_task, return_exceptions=True)

    if isinstance(audio, Exception):
        logger.warning("[engine] ASR failed: %s", audio)
        audio = {"transcript": [], "speech_analysis": {}, "success": False}
    if isinstance(frames, Exception):
        logger.warning("[engine] frame extract failed: %s", frames)
        frames = []

    t_prep = round(time.time() - t0, 1)
    logger.info("[engine] prep %.1fs: %d asr segs, %d frames", t_prep, len(audio.get("transcript", [])), len(frames))

    t1 = time.time()
    decision = await _deepseek_oneshot_decide(
        frames, audio, final_prompt, duration, profile, meta, target_duration
    )
    decide_s = round(time.time() - t1, 1)

    transcript = decision.get("transcript", audio.get("transcript", []))
    if transcript and any("keep" in s for s in transcript):
        segments = _sentences_to_segments(transcript, duration)
    else:
        segments = _normalize_segments(decision.get("segments", []), duration)
    keep_segs = [s for s in segments if s["action"] == "retain"]
    keep_dur = sum(s["time_range"][1] - s["time_range"][0] for s in keep_segs)

    result = {
        "summary": decision.get("summary", ""),
        "video_type": decision.get("video_type", ""),
        "video_style": decision.get("video_style", ""),
        "platform_applied": platform,
        "platform_label": profile["label"],
        "statistics": {
            "total_duration": round(duration, 2),
            "retained_duration": round(keep_dur, 2),
            "deleted_duration": round(duration - keep_dur, 2),
            "total_segments": len(segments),
            "retained_segments": len(keep_segs),
            "round1_duration": round(keep_dur, 2),
            "target_duration": target_duration,
            "round2_applied": False,
        },
        "suggestions": segments,
        "transcript": transcript,
        "visual_understanding": {
            "backend": f"deepseek:{DEEPSEEK_MODEL}",
            "frames": len(frames),
            "visual_summary": decision.get("visual_summary", ""),
        },
        "decision_reasoning": decision.get("reasoning", ""),
        "round2": None,
        "timings": {"prep_s": t_prep, "decide_s": decide_s, "total_s": round(time.time() - t0, 1)},
        "model": {"visual": DEEPSEEK_MODEL, "decision": DEEPSEEK_MODEL, "decision_effort": DEEPSEEK_EFFORT},
        "mock": False,
        "elapsed_s": round(time.time() - t0, 1),
    }
    logger.info("[engine] DONE oneshot: %.1fs → %.1fs (%d retain) total %.1fs",
                duration, keep_dur, len(keep_segs), result["elapsed_s"])
    return result


async def _extract_still_frames(video_path: str, duration: float, n: int) -> List[Dict[str, Any]]:
    """均匀抽 n 帧，缩到宽 512，jpeg。DeepSeek 不吃 mp4。"""
    import tempfile, base64
    n = max(8, min(int(n), 48))
    if duration <= 0:
        return []
    out_dir = tempfile.mkdtemp(prefix="ds_frames_")
    interval = max(duration / n, 0.5)
    vf = f"fps=1/{interval:.4f},scale=512:-2"
    pattern = os.path.join(out_dir, "f_%03d.jpg")
    cmd = [FFMPEG, "-y", "-loglevel", "error", "-i", video_path, "-vf", vf, "-q:v", "6", pattern]
    proc = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True, timeout=180)
    frames = []
    try:
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr[-200:] if proc.stderr else "ffmpeg frames failed")
        files = sorted(fn for fn in os.listdir(out_dir) if fn.endswith(".jpg"))
        for i, fn in enumerate(files):
            path = os.path.join(out_dir, fn)
            t = min((i + 0.5) * interval, max(duration - 0.1, 0))
            raw = open(path, "rb").read()
            frames.append({"t": round(t, 2), "b64": base64.b64encode(raw).decode("ascii")})
            try:
                os.remove(path)
            except OSError:
                pass
    finally:
        try:
            os.rmdir(out_dir)
        except OSError:
            pass
    logger.info("[engine] extracted %d frames (~every %.1fs)", len(frames), interval)
    return frames


async def _deepseek_oneshot_decide(
    frames: List[Dict[str, Any]], audio: Dict, user_prompt: str,
    duration: float, profile: Dict, meta: Dict, target_duration: Optional[float],
) -> Dict[str, Any]:
    transcript = audio.get("transcript", [])
    transcript_text = _format_transcript_for_k3(transcript)
    speech_issues = audio.get("speech_analysis", {})
    target_text = ""
    if target_duration and target_duration > 0:
        lo = max(5, int(target_duration * 0.9))
        hi = int(target_duration * ROUND2_OVERSHOOT)
        target_text = f"目标成片约 {target_duration:.0f} 秒（允许 {lo}-{hi} 秒）。一次剪到位，不要再留一轮。"

    profile_text = f"""## 目标平台：{profile['label']}
- 目标时长：{profile['target_duration']}
- 节奏：{profile['rhythm']}
- 结构：{profile['structure']}
{chr(10).join('  - ' + r for r in profile['rules'])}"""

    frame_note = "、".join(f"{i}@{fr['t']:.0f}s" for i, fr in enumerate(frames[:40]))
    prompt = f"""你是专业中文短视频剪辑师。下面是同一条视频的抽帧（每张标了时间）和带时间戳的逐字稿。
请同时看画面、读逐字稿，直接给出逐句 retain/delete，不要先写分析再另做一轮决策。

{target_text}

视频 {duration:.1f}秒，{meta['width']}x{meta['height']}。抽帧序号与大约时间：{frame_note}

## 用户意图
{user_prompt}

{profile_text}

## 逐字稿
{transcript_text}

## 语音问题
口癖 {speech_issues.get('total_fillers', 0)} 处，长停顿 {speech_issues.get('total_long_pauses', 0)} 处，重复 {speech_issues.get('total_repeats', 0)} 处。

决策要求：
1. sentences 长度必须等于逐字稿句数，idx 从 0 连续编号
2. 删口癖、长停顿、重复、导师/家长插话、口误、碎片句
3. 保住开场点题、核心情节、金句、收尾
4. 同一观点只留最完整流畅的一版
5. 结合画面：发呆/低头/摸脸/NG 的句子倾向删除

只输出 JSON：
{{
  "video_type": "口播/访谈/练习录像/...",
  "video_style": "款式",
  "summary": "1-2句",
  "visual_summary": "画面2句",
  "reasoning": "2-3句，必须同时引用画面和声音",
  "sentences": [{{"idx": 0, "action": "retain", "reason": "具体理由"}}],
  "segments": [{{"start_time": 0.0, "end_time": 5.0, "action": "retain", "label": "开场", "reason": "..."}}]
}}"""

    content_parts: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
    for i, fr in enumerate(frames):
        content_parts.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/jpeg;base64,{fr['b64']}",
                "detail": "low",
            },
        })
        content_parts.append({"type": "text", "text": f"(上图是抽帧 #{i}，约 {fr['t']:.1f}s)"})

    content = await _deepseek_stream([{"role": "user", "content": content_parts}])
    parsed = _parse_json(content)
    if parsed and transcript and parsed.get("sentences"):
        parsed["transcript"] = _apply_sentence_decisions(transcript, parsed.get("sentences", []))
        parsed["segments"] = _sentences_to_segments(parsed["transcript"], duration)
    if not parsed or "segments" not in parsed:
        parsed = _regex_extract_k3_decision(content, duration)
    if not parsed or "segments" not in parsed:
        logger.error("[engine] deepseek parse failed: %s", (content or "")[:400])
        return {
            "video_type": "未知", "video_style": "未知", "summary": "DeepSeek 决策失败",
            "reasoning": "降级全保留",
            "segments": [{"start_time": 0, "end_time": duration, "action": "retain",
                          "label": "全片", "reason": "解析失败"}],
            "transcript": transcript,
        }
    if transcript and "transcript" not in parsed:
        parsed["transcript"] = transcript
    return parsed


async def _deepseek_stream(messages: List[Dict], max_tokens: int = 32000) -> str:
    key = _read_deepseek_key()
    if not key:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "stream": True,
        "max_tokens": max_tokens,
        "reasoning_effort": DEEPSEEK_EFFORT,
        "response_format": {"type": "json_object"},
    }

    async def _read(client, body) -> str:
        parts = []
        async with client.stream(
            "POST", f"{DEEPSEEK_BASE_URL}/chat/completions", json=body, headers=headers
        ) as resp:
            if resp.status_code >= 400:
                raw = await resp.aread()
                raise RuntimeError(f"deepseek HTTP {resp.status_code}: {raw.decode()[:400]}")
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    delta = data.get("choices", [{}])[0].get("delta", {})
                    if delta.get("content"):
                        parts.append(delta["content"])
                except json.JSONDecodeError:
                    pass
        return "".join(parts)

    async with _async_httpx(
        timeout=httpx.Timeout(connect=30, read=None, write=60, pool=30),
        trust_env=False,
    ) as client:
        try:
            return await _read(client, payload)
        except RuntimeError as e:
            if "json_object" in str(e) or "response_format" in str(e):
                payload.pop("response_format", None)
                return await _read(client, payload)
            raise


async def _analyze_kimi_two_stage(video_path: str, user_prompt: str = "", custom_prompt: Optional[str] = None,
                   platform: str = "general", target_duration: Optional[float] = None) -> Dict[str, Any]:
    """
    两阶段分析：
    阶段1 并行：K3 看视频（视觉） + ASR（声音）
    阶段2 K3 综合决策（延续视觉上下文）
    """
    t0 = time.time()

    meta = _get_video_meta(video_path)
    duration = meta["duration"]
    logger.info("[engine] video: %.1fs, %dx%d, platform=%s", duration, meta["width"], meta["height"], platform)

    # ─── 阶段1：并行理解 ────────────────────────────────
    logger.info("[engine] === Phase 1: parallel understanding ===")
    t1 = time.time()

    visual_task = _visual_understanding(video_path, duration, meta)
    audio_task = _audio_understanding(video_path, duration)
    visual, audio = await asyncio.gather(visual_task, audio_task, return_exceptions=True)

    if isinstance(visual, Exception):
        logger.warning("[engine] visual understanding failed: %s", visual)
        visual = {"visual_summary": f"视觉理解失败: {str(visual)[:100]}", "shots": [], "people": [], "success": False, "file_id": None, "backend": "failed"}
    else:
        logger.info("[engine] visual done (%s): %s", visual.get("backend", "?"), visual.get("visual_summary", "")[:80])

    if isinstance(audio, Exception):
        logger.warning("[engine] audio understanding failed: %s", audio)
        audio = {"transcript": [], "speech_analysis": {}, "success": False}
    else:
        logger.info("[engine] audio done: %d transcript segs", len(audio.get("transcript", [])))
    phase1_s = round(time.time() - t1, 1)

    # ─── 阶段2：纯文本决策（不再重看视频，这是之前 13 分钟的主因） ──
    logger.info("[engine] === Phase 2: text-only decision ===")
    t2 = time.time()
    profile = get_profile(platform)
    default_prompt = "去掉所有口头禅、停顿、重复内容和废话，保留有效内容，剪成一个流畅的视频"
    final_prompt = custom_prompt or user_prompt or default_prompt

    decision = await _k3_decision(visual, audio, final_prompt, duration, profile, meta, target_duration)
    decision_s = round(time.time() - t2, 1)
    logger.info("[engine] decision done in %.1fs: %d segments", decision_s, len(decision.get("segments", [])))

    transcript = decision.get("transcript", audio.get("transcript", []))
    if transcript and any("keep" in s for s in transcript):
        segments = _sentences_to_segments(transcript, duration)
    else:
        segments = _normalize_segments(decision.get("segments", []), duration)

    keep_segs = [s for s in segments if s["action"] == "retain"]
    keep_dur = sum(s["time_range"][1] - s["time_range"][0] for s in keep_segs)
    round1_dur = keep_dur
    round2_info: Optional[Dict[str, Any]] = None
    round2_s = 0.0

    # ─── 阶段2.5：超过目标时长则二轮精简（hybrid 的二次蒸馏） ──
    if target_duration and target_duration > 0 and keep_dur > target_duration * ROUND2_OVERSHOOT:
        logger.info("[engine] === Phase 2.5: round-2 tighten %.1fs → target %.0fs ===", keep_dur, target_duration)
        t3 = time.time()
        segments, transcript, round2_info = await _second_pass_tighten(
            transcript, segments, float(target_duration), visual, final_prompt, duration
        )
        round2_s = round(time.time() - t3, 1)
        keep_segs = [s for s in segments if s["action"] == "retain"]
        keep_dur = sum(s["time_range"][1] - s["time_range"][0] for s in keep_segs)
        logger.info("[engine] round2 done in %.1fs: %.1fs → %.1fs", round2_s, round1_dur, keep_dur)

    result = {
        "summary": decision.get("summary", ""),
        "video_type": decision.get("video_type", ""),
        "video_style": decision.get("video_style", ""),
        "platform_applied": platform,
        "platform_label": profile["label"],
        "statistics": {
            "total_duration": round(duration, 2),
            "retained_duration": round(keep_dur, 2),
            "deleted_duration": round(duration - keep_dur, 2),
            "total_segments": len(segments),
            "retained_segments": len(keep_segs),
            "round1_duration": round(round1_dur, 2),
            "target_duration": target_duration,
            "round2_applied": bool(round2_info),
        },
        "suggestions": segments,
        "transcript": transcript,
        "visual_understanding": visual,
        "decision_reasoning": decision.get("reasoning", ""),
        "round2": round2_info,
        "timings": {
            "phase1_s": phase1_s,
            "decision_s": decision_s,
            "round2_s": round2_s,
            "total_s": round(time.time() - t0, 1),
        },
        "model": {
            "visual": visual.get("backend") or visual.get("model") or VISUAL_MODEL,
            "decision": DECISION_MODEL,
            "decision_effort": DECISION_EFFORT,
        },
        "mock": False,
        "elapsed_s": round(time.time() - t0, 1),
    }

    logger.info("[engine] DONE: %.1fs → %.1fs (%d retain), total %.1fs (p1=%.1f decision=%.1f r2=%.1f)",
                duration, keep_dur, len(keep_segs), result["elapsed_s"], phase1_s, decision_s, round2_s)
    return result


# ─── 阶段1a：视觉理解 ────────────────────────────────

def _visual_prompt(duration: float, meta: Dict) -> str:
    return f"""你是专业中文短视频剪辑师。请完整观看这个视频（{duration:.1f}秒，{meta['width']}x{meta['height']}）。
重点：画面/人物/动作/场景，以及能听到的废片（老师插话、口误、发呆、摸脸）。给出带时间戳的剪辑线索。

输出严格 JSON：
{{
  "visual_summary": "整体画面描述（2-3句）",
  "main_speaker_desc": "主讲人外貌/衣着",
  "shots": [{{"time_range": [0, 30], "description": "镜头描述", "visual_function": "建置/发展/高潮/过渡"}}],
  "key_moments": [
    {{"type": "hook", "start": 0, "end": 12, "desc": "开场点题"}},
    {{"type": "climax", "start": 480, "end": 493, "desc": "金句"}}
  ],
  "delete_recommendations": [
    {{"start": 50, "end": 55, "reason": "口误/老师插话/发呆"}}
  ],
  "coherence_warnings": [
    {{"start": 200, "end": 210, "warning": "此处删除可能导致话题跳跃"}}
  ],
  "people": [{{"description": "人物", "appearance_range": [0, 500]}}],
  "setting": "场景和道具",
  "cinematic": ["构图/色调/画幅/光线"]
}}

只输出 JSON。"""


async def _visual_understanding(video_path: str, duration: float, meta: Dict) -> Dict[str, Any]:
    """视觉通道：Gemini agentic。超过 80 秒走 90s 切片（Worker 整段会 524）。"""
    gemini_key = _read_gemini_key()
    if gemini_key:
        try:
            if duration > 180:
                return await _visual_understanding_gemini_chunked(video_path, duration, meta, gemini_key)
            return await _visual_understanding_gemini(video_path, duration, meta, gemini_key)
        except Exception as e:
            logger.warning("[engine] Gemini visual failed (%s), try other path", e)
            try:
                if duration > 180:
                    return await _visual_understanding_gemini(video_path, duration, meta, gemini_key)
                return await _visual_understanding_gemini_chunked(video_path, duration, meta, gemini_key)
            except Exception as e2:
                logger.warning("[engine] Gemini fallback failed (%s), fallback Moonshot", e2)

    try:
        return await _visual_understanding_k3(video_path, duration, meta, model=VISUAL_MODEL)
    except Exception as e:
        logger.warning("[engine] %s visual failed (%s), fallback kimi-k3", VISUAL_MODEL, e)
        if VISUAL_MODEL != "kimi-k3":
            return await _visual_understanding_k3(video_path, duration, meta, model="kimi-k3")
        raise


async def _visual_understanding_gemini(video_path: str, duration: float, meta: Dict, api_key: str) -> Dict[str, Any]:
    """Gemini 3.7 Flash + agentic 看视频。走代理。"""
    t0 = time.time()
    prompt = _visual_prompt(duration, meta)
    last_err = None
    for model in GEMINI_FALLBACK_MODELS:
        try:
            content = await _gemini_analyze_video(video_path, prompt, api_key, model)
            parsed = _parse_json(content) or {"visual_summary": content[:400], "shots": []}
            parsed["success"] = True
            parsed["backend"] = f"gemini:{model}"
            parsed["model"] = model
            parsed["file_id"] = None
            parsed["visual_content"] = content
            parsed["elapsed_s"] = round(time.time() - t0, 1)
            logger.info("[engine] Gemini visual ok (%s) in %.1fs", model, parsed["elapsed_s"])
            return parsed
        except Exception as e:
            last_err = e
            logger.warning("[engine] Gemini model %s failed: %s", model, e)
            if "location is not supported" in str(e).lower() or "FAILED_PRECONDITION" in str(e):
                break
    raise RuntimeError(f"Gemini visual all models failed: {last_err}")


async def _visual_understanding_gemini_chunked(
    video_path: str, duration: float, meta: Dict, api_key: str
) -> Dict[str, Any]:
    """Worker/免费档扛不住整段时，按 90 秒切片看，时间戳加偏移。"""
    import tempfile, shutil
    chunk = 90.0
    n = max(1, int((duration + chunk - 1) // chunk))
    tmp = tempfile.mkdtemp(prefix="gem_chunks_")
    summaries, deletes, moments = [], [], []
    t0 = time.time()
    try:
        for i in range(n):
            start = i * chunk
            length = min(chunk, duration - start)
            if length < 2:
                continue
            out = os.path.join(tmp, f"c{i:02d}.mp4")
            cmd = [FFMPEG, "-y", "-loglevel", "error", "-ss", str(start), "-t", str(length),
                   "-i", video_path, "-c", "copy", out]
            proc = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True, timeout=120)
            if proc.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) < 1000:
                cmd = [FFMPEG, "-y", "-loglevel", "error", "-ss", str(start), "-t", str(length),
                       "-i", video_path, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                       "-c:a", "aac", "-b:a", "64k", out]
                proc = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True, timeout=180)
            if proc.returncode != 0 or not os.path.exists(out):
                logger.warning("[engine] chunk %d extract failed", i)
                continue
            piece_meta = dict(meta)
            parsed = await _visual_understanding_gemini(out, length, piece_meta, api_key)
            summaries.append(parsed.get("visual_summary") or "")
            for rec in parsed.get("delete_recommendations") or []:
                try:
                    rec = dict(rec)
                    rec["start"] = float(rec.get("start", 0)) + start
                    rec["end"] = float(rec.get("end", 0)) + start
                    deletes.append(rec)
                except (TypeError, ValueError):
                    continue
            for m in parsed.get("key_moments") or []:
                try:
                    m = dict(m)
                    m["start"] = float(m.get("start", 0)) + start
                    m["end"] = float(m.get("end", 0)) + start
                    moments.append(m)
                except (TypeError, ValueError):
                    continue
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if not summaries and not deletes:
        raise RuntimeError("gemini chunked produced nothing")
    return {
        "success": True,
        "backend": "gemini:chunked",
        "visual_summary": " ".join(x for x in summaries if x)[:800],
        "delete_recommendations": deletes,
        "key_moments": moments,
        "elapsed_s": round(time.time() - t0, 1),
    }


def _gemini_url(path_or_url: str) -> str:
    """官方 googleapis 地址改走 gemini.hhhman.top Worker。"""
    u = path_or_url
    for host in (
        "https://generativelanguage.googleapis.com",
        "http://generativelanguage.googleapis.com",
    ):
        if u.startswith(host):
            return GEMINI_BASE_URL + u[len(host):]
    if u.startswith("http://") or u.startswith("https://"):
        return u
    return GEMINI_BASE_URL + (u if u.startswith("/") else "/" + u)


def _gemini_headers(api_key: str, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    h = {"x-goog-api-key": api_key}
    if extra:
        h.update(extra)
    return h


async def _gemini_post_with_retry(client, url: str, payload: Dict, headers: Dict, tries: int = 2):
    """503 高峰重试；524 是 Worker 墙钟，立刻放弃。"""
    last = None
    for i in range(tries):
        last = await client.post(url, json=payload, headers=headers)
        if last.status_code == 503:
            wait = 8 * (i + 1)
            logger.warning("[engine] Gemini 503, sleep %ss (%d/%d)", wait, i + 1, tries)
            await asyncio.sleep(wait)
            continue
        return last
    return last


async def _gemini_analyze_video(video_path: str, prompt: str, api_key: str, model: str) -> str:
    # Worker 已是跨境入口，不要再套 Clash，免得上传 URL 被改写丢头。
    timeout = httpx.Timeout(connect=30, read=600, write=180, pool=30)
    async with _async_httpx(timeout=timeout, trust_env=False) as client:
        file_uri, mime = await _gemini_upload(client, api_key, video_path)
        payload = {
            "contents": [{
                "role": "user",
                "parts": [
                    {
                        "fileData": {"fileUri": file_uri, "mimeType": mime},
                        "mediaProcessing": "AGENTIC",
                    },
                    {"text": prompt},
                ],
            }]
        }
        url = _gemini_url(f"/v1beta/models/{model}:generateContent")
        headers = _gemini_headers(api_key)
        resp = await _gemini_post_with_retry(client, url, payload, headers)
        if resp.status_code in (524, 504, 408):
            raise RuntimeError(f"Gemini generate {resp.status_code}: proxy timeout")
        if resp.status_code >= 400:
            logger.warning("[engine] Gemini %s HTTP %s, retry without agentic", model, resp.status_code)
            payload["contents"][0]["parts"][0].pop("mediaProcessing", None)
            resp = await _gemini_post_with_retry(client, url, payload, headers)
        if resp.status_code >= 400:
            raise RuntimeError(f"Gemini generate {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts)
        if not text:
            raise RuntimeError(f"Gemini empty response: {json.dumps(data)[:300]}")
        return text


async def _gemini_upload(client: httpx.AsyncClient, api_key: str, video_path: str) -> tuple:
    size = os.path.getsize(video_path)
    mime = "video/mp4"
    start = await client.post(
        _gemini_url("/upload/v1beta/files"),
        headers=_gemini_headers(api_key, {
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(size),
            "X-Goog-Upload-Header-Content-Type": mime,
            "Content-Type": "application/json",
        }),
        json={"file": {"display_name": os.path.basename(video_path)}},
    )
    if start.status_code >= 400:
        raise RuntimeError(f"Gemini upload start {start.status_code}: {start.text[:200]}")
    upload_url = start.headers.get("X-Goog-Upload-URL") or start.headers.get("x-goog-upload-url")
    if not upload_url:
        raise RuntimeError("Gemini upload missing X-Goog-Upload-URL")
    upload_url = _gemini_url(upload_url)

    def _read():
        with open(video_path, "rb") as f:
            return f.read()

    raw = await asyncio.to_thread(_read)
    put = await client.put(
        upload_url,
        headers={
            "Content-Length": str(size),
            "X-Goog-Upload-Offset": "0",
            "X-Goog-Upload-Command": "upload, finalize",
            "x-goog-api-key": api_key,
        },
        content=raw,
    )
    if put.status_code >= 400:
        raise RuntimeError(f"Gemini upload put {put.status_code}: {put.text[:200]}")
    info = put.json().get("file", put.json())
    file_name = info.get("name")
    file_uri = info.get("uri")
    if not file_name:
        raise RuntimeError(f"Gemini upload no file name: {put.text[:200]}")

    deadline = time.time() + 180
    while time.time() < deadline:
        state = (info.get("state") or "").upper()
        if state == "ACTIVE":
            return file_uri or f"https://generativelanguage.googleapis.com/v1beta/{file_name}", info.get("mimeType", mime)
        if state == "FAILED":
            raise RuntimeError(f"Gemini file processing failed: {info}")
        await asyncio.sleep(2)
        g = await client.get(_gemini_url(f"/v1beta/{file_name}"), headers=_gemini_headers(api_key))
        g.raise_for_status()
        info = g.json()
    raise RuntimeError("Gemini file processing timeout")


async def _visual_understanding_k3(video_path: str, duration: float, meta: Dict, model: Optional[str] = None) -> Dict[str, Any]:
    """Moonshot 上传视频并看视频。决策阶段不再复用 file_id 重看。"""
    model = model or VISUAL_MODEL
    t0 = time.time()
    logger.info("[engine] %s uploading video (%.1fMB)...", model, os.path.getsize(video_path)/1e6)
    video_file = await asyncio.to_thread(
        get_k3_client().files.create, file=open(video_path, "rb"), purpose="video"
    )
    file_id = video_file.id
    logger.info("[engine] uploaded in %.1fs, file_id=%s", time.time()-t0, file_id)

    prompt = _visual_prompt(duration, meta)
    effort = VISUAL_EFFORT if model.startswith("kimi-k3") else None
    content = await _moonshot_stream(
        [{"role": "user", "content": [
            {"type": "video_url", "video_url": {"url": f"ms://{file_id}"}},
            {"type": "text", "text": prompt},
        ]}],
        model=model,
        reasoning_effort=effort,
    )

    parsed = _parse_json(content)
    if not parsed:
        parsed = {"visual_summary": content[:300], "shots": [], "people": [], "success": False}
    parsed["success"] = True
    parsed["backend"] = f"moonshot:{model}"
    parsed["model"] = model
    parsed["file_id"] = file_id
    parsed["visual_content"] = content
    return parsed


# ─── 阶段1b：声音理解（ASR + 语音分析） ──────────────────

async def _audio_understanding(video_path: str, duration: float) -> Dict[str, Any]:
    """ASR 转录 + 语音问题分析。默认百炼云端（字级时间戳），没有 key 再走本地。"""
    use_cloud = asr_uses_cloud()
    ext = ".asr.mp3" if use_cloud else ".asr.wav"
    audio_path = video_path + ext
    if use_cloud:
        cmd = [FFMPEG, "-y", "-loglevel", "error", "-i", video_path,
               "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "128k", audio_path]
    else:
        cmd = [FFMPEG, "-y", "-loglevel", "error", "-i", video_path,
               "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", audio_path]
    proc = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"audio extract failed: {proc.stderr[-200:]}")

    try:
        if use_cloud:
            transcript = await _call_cloud_asr(audio_path)
            backend = "qwen3-bailian"
        else:
            transcript = await _call_local_asr(audio_path)
            backend = "qwen3-local"
        if transcript:
            speech_analysis = _analyze_speech(transcript, duration)
            return {"transcript": transcript, "speech_analysis": speech_analysis, "asr_backend": backend}
        logger.warning("[engine] ASR returned empty")
        return {"transcript": [], "speech_analysis": {}, "asr_backend": "none"}
    except Exception as e:
        logger.warning("[engine] ASR failed (%s): %s", "cloud" if use_cloud else "local", e)
        return {"transcript": [], "speech_analysis": {}, "asr_backend": "none"}
    finally:
        try:
            os.remove(audio_path)
        except OSError:
            pass


async def _call_cloud_asr(audio_path: str) -> List[Dict]:
    """百炼 qwen3-asr-flash-filetrans，enable_words=字级时间戳。"""
    key = _read_dashscope_key()
    if not key:
        raise RuntimeError("缺少 DASHSCOPE_API_KEY / BAILIAN_API_KEY")
    headers_auth = {"Authorization": f"Bearer {key}"}
    timeout = httpx.Timeout(connect=30, read=120, write=120, pool=30)

    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        with open(audio_path, "rb") as f:
            files = {"file": (os.path.basename(audio_path), f, "audio/mpeg")}
            up = await client.post(
                f"{BAILIAN_BASE_URL}/api/v1/files",
                headers=headers_auth,
                files=files,
                data={"purpose": "transcription"},
            )
        if up.status_code >= 400:
            raise RuntimeError(f"bailian upload {up.status_code}: {up.text[:300]}")
        file_id = (up.json().get("data") or {}).get("uploaded_files", [{}])[0].get("file_id")
        if not file_id:
            raise RuntimeError(f"bailian upload no file_id: {up.text[:300]}")

        info = await client.get(f"{BAILIAN_BASE_URL}/api/v1/files/{file_id}", headers=headers_auth)
        if info.status_code >= 400:
            raise RuntimeError(f"bailian file url {info.status_code}: {info.text[:300]}")
        file_url = (info.json().get("data") or {}).get("url") or info.json().get("url")
        if not file_url:
            raise RuntimeError("bailian file url empty")

        submit = await client.post(
            f"{BAILIAN_BASE_URL}/api/v1/services/audio/asr/transcription",
            headers={
                **headers_auth,
                "Content-Type": "application/json",
                "X-DashScope-Async": "enable",
                "X-DashScope-OssResourceResolve": "enable",
            },
            json={
                "model": BAILIAN_ASR_MODEL,
                "input": {"file_url": file_url},
                "parameters": {
                    "language_hints": ["zh"],
                    "enable_itn": False,
                    "enable_words": True,
                },
            },
        )
        if submit.status_code >= 400:
            raise RuntimeError(f"bailian asr submit {submit.status_code}: {submit.text[:300]}")
        task_id = (submit.json().get("output") or {}).get("task_id")
        if not task_id:
            raise RuntimeError(f"bailian asr no task_id: {submit.text[:300]}")

        raw = None
        deadline = time.time() + 300
        while time.time() < deadline:
            poll = await client.get(f"{BAILIAN_BASE_URL}/api/v1/tasks/{task_id}", headers=headers_auth)
            poll.raise_for_status()
            data = poll.json()
            status = (data.get("output") or {}).get("task_status") or data.get("status")
            if status in ("SUCCEEDED", "SUCCESS", "COMPLETED"):
                raw = data
                break
            if status in ("FAILED", "ERROR", "CANCELLED"):
                raise RuntimeError(f"bailian asr {status}: {json.dumps(data, ensure_ascii=False)[:400]}")
            await asyncio.sleep(3)
        if raw is None:
            raise TimeoutError("bailian asr timeout")

    transcripts = (raw.get("output") or {}).get("results") or (raw.get("output") or {}).get("transcripts") or []
    if not transcripts:
        turl = (
            ((raw.get("output") or {}).get("result") or {}).get("transcription_url")
            or (raw.get("output") or {}).get("transcription_url")
        )
        if turl:
            async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
                tr = await client.get(turl)
                tr.raise_for_status()
                transcripts = tr.json().get("transcripts") or []

    segments = []
    for ch in transcripts:
        for sent in ch.get("sentences", []):
            text = (sent.get("text") or "").strip()
            if not text:
                continue
            begin = float(sent.get("begin_time", 0) or 0) / 1000.0
            end = float(sent.get("end_time", 0) or 0) / 1000.0
            words = []
            for w in sent.get("words") or []:
                ws = float(w.get("begin_time", 0) or 0) / 1000.0
                we = float(w.get("end_time", 0) or 0) / 1000.0
                words.append({
                    "word": w.get("text") or w.get("word") or "",
                    "start": round(ws, 3),
                    "end": round(we, 3),
                })
            segments.append({
                "text": text,
                "start": round(begin, 3),
                "end": round(end, 3),
                "words": words,
            })
    logger.info("[engine] bailian ASR: %d segments, %d words", len(segments), sum(len(s["words"]) for s in segments))
    return segments


async def _call_local_asr(audio_path: str) -> List[Dict]:
    """调本地 Qwen3-ASR 服务（字符级时间戳）"""
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=600, write=30, pool=30)) as client:
        h = await client.get(f"{ASR_URL}/health", timeout=5)
        if h.status_code != 200:
            raise RuntimeError(f"ASR health {h.status_code}")
        resp = await client.post(
            f"{ASR_URL}/transcribe",
            json={"audio_path": audio_path, "language": "zh", "hotwords": ASR_HOTWORDS},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"ASR {resp.status_code}")
        data = resp.json()

    segments = []
    for s in data.get("segments", []):
        text = (s.get("text") or "").strip()
        if not text:
            continue
        segments.append({
            "text": text,
            "start": round(float(s.get("start", 0)), 3),
            "end": round(float(s.get("end", 0)), 3),
            "words": [
                {"word": w.get("word", ""),
                 "start": round(float(w.get("start", 0)), 3),
                 "end": round(float(w.get("end", 0)), 3)}
                for w in s.get("words", [])
            ],
        })
    logger.info("[engine] local ASR: %d segments", len(segments))
    return segments


def _analyze_speech(transcript: List[Dict], duration: float) -> Dict[str, Any]:
    """从逐字稿分析语音问题（口癖/停顿/重复/说话人变化）"""
    import re
    from difflib import SequenceMatcher
    FILLER = re.compile(r"^(呃|额|嗯|啊|呢|吧|嘛|那个|然后|对吧|就是说|然后呢|所以说|哎呀|哈|呵|哦|噢|唔)+[。！？!?…\s]*$")

    issues = {"fillers": [], "long_pauses": [], "repeats": [], "speakers": set()}
    prev_text = ""
    for i, seg in enumerate(transcript):
        text = seg.get("text", "").strip()
        if FILLER.match(text):
            issues["fillers"].append({"idx": i, "time": seg.get("start", 0), "content": text})
        if i > 0:
            gap = seg.get("start", 0) - transcript[i-1].get("end", 0)
            if gap > 1.5:
                issues["long_pauses"].append({"between": [i-1, i], "gap": round(gap, 2)})
        if prev_text and len(text) < len(prev_text):
            if SequenceMatcher(None, text, prev_text).ratio() > 0.8:
                issues["repeats"].append({"idx": i, "content": text[:30]})
        prev_text = text
        if seg.get("speaker"):
            issues["speakers"].add(seg["speaker"])

    issues["speakers"] = list(issues["speakers"])
    issues["total_fillers"] = len(issues["fillers"])
    issues["total_long_pauses"] = len(issues["long_pauses"])
    issues["total_repeats"] = len(issues["repeats"])
    return issues


# ─── 阶段2：K3 综合决策（延续视觉上下文） ──────────────────

async def _k3_decision(visual: Dict, audio: Dict, user_prompt: str,
                        duration: float, profile: Dict, meta: Dict,
                        target_duration: Optional[float] = None) -> Dict[str, Any]:
    """
    K3 延续阶段1 视觉对话上下文，加入声音理解 + 用户意图 + 平台偏好，做剪辑决策。
    关键：视觉 file_id 复用，K3 不用重新看视频。
    输出每个 transcript 句子的 keep/delete 状态，供文本剪辑使用。
    """
    # 格式化声音理解
    transcript = audio.get("transcript", [])
    transcript_text = _format_transcript_for_k3(transcript)
    speech_issues = audio.get("speech_analysis", {})

    # 平台偏好
    profile_text = f"""## 目标平台：{profile['label']}
- 目标时长：{profile['target_duration']}
- 画幅：{profile['aspect_ratio']}
- 节奏：{profile['rhythm']}
- 结构：{profile['structure']}
- 平台规则：
{chr(10).join('  - ' + r for r in profile['rules'])}"""

    visual_content = visual.get("visual_content") or json.dumps(
        {k: visual.get(k) for k in ("visual_summary", "key_moments", "delete_recommendations", "coherence_warnings", "shots") if visual.get(k)},
        ensure_ascii=False,
    )

    target_text = ""
    if target_duration and target_duration > 0:
        lo = max(5, int(target_duration * 0.9))
        hi = int(target_duration * ROUND2_OVERSHOOT)
        target_text = f"目标成片时长约 {target_duration:.0f} 秒（允许 {lo}-{hi} 秒）。超过上限必须再删，不要为了短而丢掉开场/金句/收尾。"

    decision_prompt = f"""你在上一轮已经看过这个视频并输出了视觉理解。现在请综合视觉理解 + 声音理解 + 用户意图 + 款式判断 + 平台偏好，做剪辑决策。

{target_text}

## 你的视觉理解（上一轮输出）
{visual_content[:6000]}

## 声音理解（ASR 转录 + 语音分析）
### 逐字稿（带时间戳）
{transcript_text}

### 语音问题检测
- 口癖句：{speech_issues.get('total_fillers', 0)} 处
- 长停顿（>1.5s）：{speech_issues.get('total_long_pauses', 0)} 处
- 重复句：{speech_issues.get('total_repeats', 0)} 处
- 说话人数：{len(speech_issues.get('speakers', []))}

## 用户剪辑意图
{user_prompt}

{profile_text}

视频元信息：{duration:.1f}秒，{meta['width']}x{meta['height']}，{meta['fps']:.1f}fps

输出严格的 JSON：
{{
  "video_type": "你判断的视频类型（口播/访谈/练习录像/Vlog/教程/产品演示/会议/播客/其他）",
  "video_style": "你判断的视频款式（如：少儿讲书练习/职场口播/技术教程等）",
  "summary": "视频核心内容概括（1-2句）",
  "reasoning": "你的剪辑决策思路（2-3句，说明你怎么综合视觉+声音+款式+平台做的决策）",
  "sentences": [
    {{
      "idx": 0,
      "action": "retain" | "delete",
      "reason": "保留/删除理由（必须结合视觉或声音理解的具体内容）"
    }}
  ],
  "segments": [
    {{
      "start_time": 0.0,
      "end_time": 5.0,
      "action": "retain" | "delete",
      "label": "中文标签",
      "reason": "保留/删除理由"
    }}
  ]
}}

决策要求：
1. sentences 数组长度必须等于下面给出的 transcript 句子数量。每个句子对应上面的 [idx]
2. segments 必须覆盖完整时间轴 [0, {duration:.2f}]，不重叠不遗漏
3. 结合视觉理解判断画面价值（构图/光线/人物状态/场景）
4. 结合声音理解判断内容价值（口癖/停顿/重复/说话人）
5. 删除：口癖、长停顿、重复表达、画外音/导师提问、口误、碎片句、与平台不符的内容
6. 保留：有效内容、叙事连贯、符合平台节奏
7. 同一观点多次表达只保留最完整流畅的一版
8. 凡是导师/采访者说话的段落一律删除（除非用户明确要求保留）
9. action 只能是 "retain" 或 "delete"
10. reason 必须具体，引用视觉或声音理解的内容
11. 适配目标平台：{profile['label']} 的时长/节奏偏好

只输出 JSON。"""

    # 纯文本决策：不再把视频 file_id 送回去。hybrid 已验证文本报告足够，且能避开 13 分钟重看。
    content = await _moonshot_stream(
        [{"role": "user", "content": decision_prompt}],
        model=DECISION_MODEL,
        reasoning_effort=DECISION_EFFORT if DECISION_MODEL.startswith("kimi-k3") else None,
    )
    parsed = _parse_json(content)
    if parsed and transcript and parsed.get("sentences"):
        parsed["transcript"] = _apply_sentence_decisions(transcript, parsed.get("sentences", []))
        parsed["segments"] = _sentences_to_segments(parsed["transcript"], duration)
    if parsed and "segments" in parsed and transcript and "transcript" not in parsed:
        parsed["transcript"] = transcript
    if not parsed or "segments" not in parsed:
        logger.warning("[engine] K3 JSON parse failed, trying regex extraction")
        parsed = _regex_extract_k3_decision(content, duration)
    if not parsed or "segments" not in parsed:
        logger.error("[engine] K3 response parse failed, raw: %s", content[:500])
        return {
            "video_type": "未知", "video_style": "未知", "summary": "K3 决策失败",
            "reasoning": "降级：全部保留，请手动调整",
            "segments": [{"start_time": 0, "end_time": duration, "action": "retain",
                          "label": "全片", "reason": "K3 解析失败降级保留"}],
            "transcript": transcript,
        }
    return parsed


# ─── Moonshot stream 调用 ──────────────────────────────────

async def _moonshot_stream(messages: List[Dict], model: str = K3_MODEL,
                           reasoning_effort: Optional[str] = None,
                           max_tokens: int = 64000) -> str:
    """Moonshot stream。K3 不传 temperature（官方固定值）。国内直连，不走代理。"""
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": True,
    }
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    headers = {"Authorization": f"Bearer {_read_moonshot_key()}", "Content-Type": "application/json"}

    content_parts = []
    async with _async_httpx(
        timeout=httpx.Timeout(connect=30, read=None, write=30, pool=30),
        trust_env=False,
    ) as client:
        async with client.stream("POST", f"{MOONSHOT_BASE_URL}/chat/completions",
                                   json=payload, headers=headers) as resp:
            if resp.status_code >= 400:
                body = await resp.aread()
                raise RuntimeError(f"{model} HTTP {resp.status_code}: {body.decode()[:300]}")
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    delta = data.get("choices", [{}])[0].get("delta", {})
                    if delta.get("content"):
                        content_parts.append(delta["content"])
                except json.JSONDecodeError:
                    pass
    return "".join(content_parts)


async def _k3_stream(messages: List[Dict], max_tokens: int = 64000) -> str:
    """兼容旧调用。"""
    return await _moonshot_stream(messages, model=DECISION_MODEL, reasoning_effort=DECISION_EFFORT, max_tokens=max_tokens)


# ─── 二轮精简（hybrid 二次蒸馏，纯文本） ──────────────

async def _second_pass_tighten(
    transcript: List[Dict],
    segments: List[Dict],
    target: float,
    visual: Dict,
    user_prompt: str,
    duration: float,
) -> tuple:
    """只从已保留句里再删，对齐到目标时长。失败则原样返回。"""
    if not transcript:
        return segments, transcript, {"applied": False, "reason": "no transcript"}

    kept = [(i, s) for i, s in enumerate(transcript) if s.get("keep", True)]
    keep_dur = sum(float(s.get("end", 0)) - float(s.get("start", 0)) for _, s in kept)
    if keep_dur <= target * ROUND2_OVERSHOOT:
        return segments, transcript, {"applied": False, "reason": "already in range"}

    lines = []
    for i, s in kept:
        lines.append(f"[{i}] [{s.get('start', 0):.1f}-{s.get('end', 0):.1f}s] {s.get('text', '').strip()}")
    visual_brief = visual.get("visual_summary", "")
    gold = visual.get("key_moments") or []

    prompt = f"""第一轮成片 {keep_dur:.0f} 秒，目标 {target:.0f} 秒（允许到 {target * ROUND2_OVERSHOOT:.0f} 秒）。
用户意图：{user_prompt}
画面摘要：{visual_brief}
关键时刻：{json.dumps(gold, ensure_ascii=False)[:1500]}

下面是第一轮保留的句子（原始时间轴）。请再删掉口癖、重复、导师声、注水句，使总时长落入目标。
必须保住：开场点题、核心情节转折、金句、收尾。不要加回已删除的内容。

{chr(10).join(lines)[:18000]}

只输出 JSON：
{{
  "critique": "第一轮主要问题",
  "delete_idx": [3, 7, 12],
  "reason": "为什么删这些"
}}
delete_idx 必须是上面的句子 idx。"""

    try:
        content = await _moonshot_stream(
            [{"role": "user", "content": prompt}],
            model=DECISION_MODEL,
            reasoning_effort=ROUND2_EFFORT if DECISION_MODEL.startswith("kimi-k3") else None,
            max_tokens=16000,
        )
        parsed = _parse_json(content) or {}
        delete_idx = set(int(x) for x in parsed.get("delete_idx", []) if str(x).lstrip("-").isdigit())
        if not delete_idx:
            return segments, transcript, {"applied": False, "reason": "round2 empty", "raw": content[:400]}
        new_transcript = []
        for i, s in enumerate(transcript):
            item = dict(s)
            if i in delete_idx:
                item["keep"] = False
                item["reason"] = (item.get("reason") or "") + " | round2: " + parsed.get("reason", "超时精简")
            new_transcript.append(item)
        new_segments = _sentences_to_segments(new_transcript, duration)
        new_dur = sum(x["time_range"][1] - x["time_range"][0] for x in new_segments if x["action"] == "retain")
        # 防 round2 删过头：成片短于目标 70% 则放弃
        if new_dur < target * 0.7:
            logger.warning("[engine] round2 too aggressive %.1fs < 70%% of target, keep round1", new_dur)
            return segments, transcript, {"applied": False, "reason": "too aggressive", "would_be": new_dur}
        return new_segments, new_transcript, {
            "applied": True,
            "critique": parsed.get("critique", ""),
            "reason": parsed.get("reason", ""),
            "deleted": len(delete_idx),
            "before_s": round(keep_dur, 1),
            "after_s": round(new_dur, 1),
        }
    except Exception as e:
        logger.warning("[engine] round2 failed: %s", e)
        return segments, transcript, {"applied": False, "reason": str(e)}


# ─── 渲染 ──────────────────────────────────────────

def _polish_vf(polish: Optional[Dict], subtitle_srt: Optional[str] = None) -> str:
    """美颜/滤镜/字幕一条滤镜链。"""
    polish = polish or {}
    fs: List[str] = []
    beauty = int(polish.get("beauty") or 0)
    if beauty > 0:
        d = 1.2 + beauty / 28.0
        fs.append(f"hqdn3d={d:.1f}:{d * 0.75:.1f}:{d * 1.3:.1f}:{d:.1f}")
        fs.append(f"eq=brightness={beauty / 480:.3f}:saturation={1 + beauty / 380:.3f}")
    look = polish.get("look") or "none"
    if look == "warm":
        fs.append("eq=gamma_r=1.05:gamma_b=0.94:saturation=1.08")
    elif look == "cool":
        fs.append("eq=gamma_b=1.06:gamma_r=0.96:saturation=0.98")
    elif look == "bright":
        fs.append("eq=brightness=0.07:contrast=1.06:saturation=1.05")
    if subtitle_srt and os.path.exists(subtitle_srt):
        abs_p = os.path.abspath(subtitle_srt).replace("\\", "/").replace(":", "\\:")
        style = polish.get("caption_style") or "douyin"
        if style == "douyin":
            force = "Fontsize=42,PrimaryColour=&H0000E1FF,OutlineColour=&H00000000,Outline=3,Alignment=2,MarginV=80,Bold=1"
        else:
            force = "Fontsize=40,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=2,Alignment=2,MarginV=80"
        fs.append(f"subtitles='{abs_p}':force_style='{force}'")
    return ",".join(fs)


async def export(plan: Dict, video_path: str, output_path: str,
                  subtitle_srt: Optional[str] = None, polish: Optional[Dict] = None) -> Dict:
    """ffmpeg concat 拼接保留片段 + 美颜/滤镜/字幕烧录"""
    polish = polish or {}
    segments = plan.get("segments", plan.get("suggestions", []))
    retain = [s for s in segments if s.get("action") == "retain"]
    if not retain:
        raise RuntimeError("no retain segments")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    import tempfile
    temp_dir = tempfile.mkdtemp(prefix="render_")
    clip_files = []
    sub_mode = "none"
    fade = (polish.get("transition") or "cut") == "fade"
    try:
        for i, seg in enumerate(retain):
            tr = seg.get("time_range", [seg.get("start_time", 0), seg.get("end_time", 0)])
            clip_path = os.path.join(temp_dir, f"clip_{i:04d}.mp4")
            await _cut_clip(video_path, tr[0], tr[1] - tr[0], clip_path, fade=fade)
            clip_files.append(clip_path)

        concat_path = os.path.join(temp_dir, "concat.txt")
        with open(concat_path, "w") as f:
            for cf in clip_files:
                f.write(f"file '{cf}'\n")

        intermediate = output_path + ".intermediate.mp4"
        cmd = [FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
               "-i", concat_path, "-c:v", "libx264", "-preset", "fast", "-crf", "18",
               "-c:a", "aac", "-b:a", "128k", intermediate]
        proc = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            raise RuntimeError(f"concat failed: {proc.stderr[-300:]}")

        vf = _polish_vf(polish, subtitle_srt if (polish.get("caption_style") != "none") else None)
        if vf:
            cmd = [FFMPEG, "-y", "-loglevel", "error", "-i", intermediate,
                   "-vf", vf, "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                   "-c:a", "aac", "-b:a", "128k", output_path]
            proc = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True, timeout=600)
            if proc.returncode == 0:
                os.remove(intermediate)
                sub_mode = "hard" if "subtitles=" in vf else "look"
            else:
                logger.warning("[render] polish vf failed: %s", proc.stderr[-240:])
                os.rename(intermediate, output_path)
        else:
            os.rename(intermediate, output_path)
    finally:
        for cf in clip_files:
            try: os.remove(cf)
            except: pass
        try: os.remove(concat_path)
        except: pass
        try: os.rmdir(temp_dir)
        except: pass

    meta = _get_video_meta(output_path)
    return {
        "output_path": output_path,
        "duration": meta.get("duration", 0),
        "size_bytes": os.path.getsize(output_path) if os.path.exists(output_path) else 0,
        "kept_segments": len(retain),
        "subtitle_mode": sub_mode,
    }


async def _cut_clip(source: str, start: float, duration: float, output: str, fade: bool = False) -> None:
    cmd = [FFMPEG, "-y", "-loglevel", "error", "-ss", str(start), "-i", source,
           "-t", str(duration), "-c:v", "libx264", "-preset", "fast", "-crf", "18",
           "-c:a", "aac", "-b:a", "128k", "-avoid_negative_ts", "make_zero"]
    if fade and duration > 0.3:
        fd = min(0.12, duration / 4)
        cmd.extend(["-vf", f"fade=t=in:st=0:d={fd:.3f},fade=t=out:st={duration - fd:.3f}:d={fd:.3f}"])
    cmd.append(output)
    proc = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(f"cut clip failed: {proc.stderr[-300:]}")


# ─── 字幕：用阶段1声音通道的逐字稿生成逐字字幕 SRT ──

def generate_caption_srt(segments: List[Dict], captions: List[Dict], output_path: str) -> str:
    """把客户改过的字幕行，按成片时间轴排成 SRT。"""
    retain_segs = [s for s in segments if s.get("action") == "retain"]
    lines = []
    idx = 1
    output_time = 0.0
    for seg in retain_segs:
        tr = seg.get("time_range", [seg.get("start_time", 0), seg.get("end_time", 0)])
        for cap in captions:
            if not cap.get("on", True):
                continue
            t_start = float(cap.get("start", 0))
            t_end = float(cap.get("end", 0))
            if t_end <= tr[0] or t_start >= tr[1]:
                continue
            text = (cap.get("text") or "").strip()
            if not text:
                continue
            dur = max(0.2, min(t_end, tr[1]) - max(t_start, tr[0]))
            lines.append(str(idx))
            lines.append(f"{_srt_time(output_time)} --> {_srt_time(output_time + dur)}")
            lines.append(text)
            lines.append("")
            idx += 1
            output_time += dur
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return output_path


def generate_subtitle_srt(segments: List[Dict], transcript: List[Dict], output_path: str) -> str:
    """用阶段1声音通道的逐字稿生成逐字字幕 SRT，时间轴按成片重排"""
    retain_segs = [s for s in segments if s.get("action") == "retain"]
    lines = []
    idx = 1
    output_time = 0.0
    for seg in retain_segs:
        tr = seg.get("time_range", [seg.get("start_time", 0), seg.get("end_time", 0)])
        for t_seg in transcript:
            t_start = t_seg.get("start", 0)
            t_end = t_seg.get("end", 0)
            if t_start >= tr[0] and t_end <= tr[1]:
                text = (t_seg.get("text") or "").strip()
                if not text:
                    continue
                dur = t_end - t_start
                lines.append(str(idx))
                lines.append(f"{_srt_time(output_time)} --> {_srt_time(output_time + dur)}")
                lines.append(text)
                lines.append("")
                idx += 1
                output_time += dur
    srt = "\n".join(lines)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(srt)
    return output_path


# ─── 工具 ──────────────────────────────────────────

def _get_video_meta(path: str) -> Dict:
    cmd = [FFPROBE, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", path]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    data = json.loads(proc.stdout)
    vs = next((s for s in data.get("streams", []) if s["codec_type"] == "video"), {})
    fmt = data.get("format", {})
    fps_str = vs.get("r_frame_rate", "30/1")
    num, den = fps_str.split("/")
    fps = float(num) / float(den) if float(den) != 0 else 30.0
    return {"duration": float(fmt.get("duration", 0)), "width": int(vs.get("width", 0)),
            "height": int(vs.get("height", 0)), "fps": fps}


def _has_libass() -> bool:
    try:
        proc = subprocess.run([FFMPEG, "-filters"], capture_output=True, text=True, timeout=10)
        blob = (proc.stdout or "") + (proc.stderr or "")
        return "subtitles" in blob
    except Exception:
        return False


def _parse_json(text: str) -> Optional[Dict]:
    if not text:
        return None
    import re
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    if "{" in text:
        s = text.find("{")
        depth = 0
        e = s
        for i, ch in enumerate(text[s:], start=s):
            if ch == "{": depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    e = i
                    break
        if depth == 0:
            candidate = text[s:e+1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                cleaned = re.sub(r",\s*([}\]])", r"\1", candidate)
                try:
                    return json.loads(cleaned)
                except json.JSONDecodeError:
                    pass
    return None


def _regex_extract_k3_decision(content: str, duration: float) -> Dict[str, Any]:
    """K3 JSON 解析失败的降级方案：regex 提取关键字段和 segments"""
    import re
    result = {"video_type": "", "video_style": "", "summary": "", "reasoning": "", "segments": []}
    for key in ["video_type", "video_style", "summary", "reasoning"]:
        m = re.search(rf'"{key}"\s*:\s*"((?:[^"\\]|\\.)*)"', content)
        if m:
            try:
                result[key] = m.group(1).encode().decode('unicode_escape').encode('latin-1').decode('utf-8')
            except Exception:
                result[key] = m.group(1)
    seg_pattern = re.compile(
        r'\{\s*"start_time"\s*:\s*([\d.]+)\s*,\s*'
        r'"end_time"\s*:\s*([\d.]+)\s*,\s*'
        r'"action"\s*:\s*"(retain|delete)"\s*,?\s*'
        r'"label"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,?\s*'
        r'"reason"\s*:\s*"((?:[^"\\]|\\.)*)"\s*\}',
        re.DOTALL
    )
    for m in seg_pattern.finditer(content):
        result["segments"].append({
            "start_time": float(m.group(1)), "end_time": float(m.group(2)),
            "action": m.group(3), "label": m.group(4), "reason": m.group(5),
        })
    logger.info("[engine] regex extracted %d segments", len(result["segments"]))
    return result


def _normalize_segments(raw: List[Dict], duration: float) -> List[Dict]:
    segs = []
    for s in raw:
        start = float(s.get("start_time", s.get("start", 0)))
        end = float(s.get("end_time", s.get("end", 0)))
        action = s.get("action", "retain" if s.get("keep") else "delete")
        if action not in ("retain", "delete"):
            action = "retain" if s.get("keep") else "delete"
        if end <= start:
            continue
        segs.append({
            "time_range": [round(start, 2), round(min(end, duration), 2)],
            "action": action,
            "label": s.get("label", ""),
            "reason": s.get("reason", ""),
        })
    segs.sort(key=lambda x: x["time_range"][0])
    if segs:
        filled = []
        prev_end = 0.0
        for s in segs:
            if s["time_range"][0] > prev_end + 0.1:
                filled.append({"time_range": [round(prev_end, 2), round(s["time_range"][0], 2)],
                               "action": "delete", "label": "未覆盖区", "reason": "gap fill"})
            filled.append(s)
            prev_end = s["time_range"][1]
        if prev_end < duration - 0.1:
            filled.append({"time_range": [round(prev_end, 2), round(duration, 2)],
                           "action": "delete", "label": "未覆盖区", "reason": "gap fill"})
        segs = filled
    return segs


def _sentences_to_segments(transcript: List[Dict], duration: float) -> List[Dict]:
    """句子 keep/delete → 时间轴片段，优先于模型自己吐的 segments。"""
    segs: List[Dict] = []
    current_keep = None
    current_start = 0.0
    current_end = 0.0
    labels: List[str] = []
    reasons: List[str] = []

    for sent in transcript:
        keep = bool(sent.get("keep", True))
        start = float(sent.get("start", 0))
        end = float(sent.get("end", 0))
        if start >= end:
            continue
        text = str(sent.get("text", "")).strip()[:20]
        if current_keep is None:
            current_keep = keep
            current_start = start
            current_end = end
            labels = [text]
            reasons = [str(sent.get("reason", ""))]
        elif keep == current_keep and start <= current_end + 0.35:
            current_end = max(current_end, end)
            if text:
                labels.append(text)
            if sent.get("reason"):
                reasons.append(str(sent.get("reason")))
        else:
            segs.append({
                "start_time": round(current_start, 2),
                "end_time": round(current_end, 2),
                "action": "retain" if current_keep else "delete",
                "label": " + ".join([x for x in labels if x][:3]) or ("保留" if current_keep else "删除"),
                "reason": next((r for r in reasons if r), ""),
            })
            current_keep = keep
            current_start = start
            current_end = end
            labels = [text]
            reasons = [str(sent.get("reason", ""))]

    if current_keep is not None:
        segs.append({
            "start_time": round(current_start, 2),
            "end_time": round(current_end, 2),
            "action": "retain" if current_keep else "delete",
            "label": " + ".join([x for x in labels if x][:3]) or ("保留" if current_keep else "删除"),
            "reason": next((r for r in reasons if r), ""),
        })
    return _normalize_segments(segs, duration)


def _apply_sentence_decisions(transcript: List[Dict], sentences: List[Dict]) -> List[Dict]:
    """把 K3 输出的句子级决策合并到 transcript 中。"""
    out = []
    for i, sent in enumerate(transcript):
        decision = next((s for s in sentences if int(s.get("idx", -1)) == i), None)
        keep = True
        reason = ""
        if decision:
            keep = decision.get("action", "retain") == "retain"
            reason = decision.get("reason", "")
        out.append({
            "text": sent.get("text", ""),
            "start": float(sent.get("start", 0)),
            "end": float(sent.get("end", 0)),
            "words": sent.get("words", []),
            "keep": keep,
            "reason": reason,
        })
    return out


def _format_transcript_for_k3(transcript: List[Dict]) -> str:
    if not transcript:
        return "(无逐字稿)"
    lines = []
    for i, seg in enumerate(transcript):
        text = seg.get("text", "").strip()
        if not text:
            continue
        start = seg.get("start", 0)
        end = seg.get("end", 0)
        spk = seg.get("speaker", "")
        spk_tag = f"[{spk}]" if spk else ""
        lines.append(f"[{i}] {spk_tag}[{start:.1f}-{end:.1f}s] {text}")
    return "\n".join(lines)[:20000]


def _srt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"