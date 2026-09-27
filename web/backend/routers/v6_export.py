"""
v6 K3 多模态分析与导出路由。

分析：调用 engine_v6.analyze（视觉+声音理解 → K3 决策）
导出：调用 engine_v6.export（ffmpeg concat + 可选字幕）
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import FileResponse

import models
from web.backend import dependencies as deps
from web.backend import engine_v6

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects/{project_id}", tags=["v6"])


def _v6_segments_to_model(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 engine_v6 的 suggestions 转成当前前端/Project 用的 segment 格式。"""
    result: list[dict[str, Any]] = []
    for seg in segments:
        tr = seg.get("time_range", [0, 0])
        action = seg.get("action", "retain")
        result.append({
            "start_time": float(tr[0]) if isinstance(tr, (list, tuple)) else float(seg.get("start_time", 0)),
            "end_time": float(tr[1]) if isinstance(tr, (list, tuple)) else float(seg.get("end_time", 0)),
            "keep": action == "retain",
            "label": seg.get("label", ""),
            "confidence": 0.9,
        })
    return result


async def _run_v6_analyze_task(project_id: str, user_prompt: str, platform: str, target_duration: Optional[float]) -> None:
    """后台执行 v6 两阶段分析。"""
    deps.set_task_status(project_id, "analyze", "running", progress=5)
    try:
        project = deps.load_project(project_id)
        video_path = project.video_path

        deps.set_task_status(project_id, "analyze", "running", progress=10)
        v6_result = await engine_v6.analyze(
            video_path=video_path,
            user_prompt=user_prompt,
            platform=platform,
            target_duration=target_duration,
        )

        suggestions = v6_result.get("suggestions", [])
        segments = _v6_segments_to_model(suggestions)

        # 保存 transcript 到项目，供文本剪辑和字幕用
        transcript = v6_result.get("transcript", [])
        if transcript:
            import json
            transcript_path = deps._project_dir(project_id) / "transcript.json"
            transcript_path.write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")
            project.transcript = transcript
        pack = v6_result.get("pack") or {}
        if pack:
            import json
            (deps._project_dir(project_id) / "pack.json").write_text(
                json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        caps = [
            {"start": float(s.get("start", 0)), "end": float(s.get("end", 0)),
             "text": str(s.get("text") or ""), "on": True}
            for s in (transcript or []) if s.get("keep", True) and str(s.get("text") or "").strip()
        ]
        old_p = project.polish or {}
        project.polish = {
            "caption_style": old_p.get("caption_style") or "douyin",
            "beauty": old_p.get("beauty") or 0,
            "look": old_p.get("look") or "none",
            "transition": old_p.get("transition") or "cut",
            "captions": caps,
        }

        project.segments = [
            models.Segment(
                start_time=s["start_time"],
                end_time=s["end_time"],
                keep=s["keep"],
                label=s["label"],
                confidence=s["confidence"],
            )
            for s in segments
        ]
        deps.save_project(project_id, project)

        deps.set_task_status(
            project_id,
            "analyze",
            "done",
            progress=100,
            result={
                "segments": [seg.to_dict() for seg in project.segments],
                "summary": v6_result.get("summary", ""),
                "video_type": v6_result.get("video_type", ""),
                "video_style": v6_result.get("video_style", ""),
                "statistics": v6_result.get("statistics", {}),
                "elapsed_s": v6_result.get("elapsed_s", 0),
            },
        )
        logger.info("v6 分析完成: %s, %d segments", project_id, len(segments))
    except Exception as exc:
        logger.error("v6 分析失败: %s", exc)
        deps.set_task_status(project_id, "analyze", "error", progress=0, error=str(exc))


def _load_transcript(project_id: str) -> list[dict[str, Any]]:
    import json
    transcript_path = deps._project_dir(project_id) / "transcript.json"
    if transcript_path.exists():
        try:
            return json.loads(transcript_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _transcript_to_segments(transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把句子级 keep/delete 状态合并成 retain/delete segments。"""
    if not transcript:
        return []
    segs: list[dict[str, Any]] = []
    current_keep = None
    current_start = 0.0
    current_end = 0.0
    labels: list[str] = []

    for sent in transcript:
        keep = bool(sent.get("keep", True))
        start = float(sent.get("start", 0))
        end = float(sent.get("end", 0))
        if start >= end:
            continue

        if current_keep is None:
            current_keep = keep
            current_start = start
            current_end = end
            labels.append(str(sent.get("text", ""))[:20])
        elif keep == current_keep and start <= current_end + 0.3:
            current_end = end
            labels.append(str(sent.get("text", ""))[:20])
        else:
            segs.append({
                "time_range": [round(current_start, 2), round(current_end, 2)],
                "action": "retain" if current_keep else "delete",
                "label": " + ".join(labels[:3]) or ("保留" if current_keep else "删除"),
            })
            current_keep = keep
            current_start = start
            current_end = end
            labels = [str(sent.get("text", ""))[:20]]

    if current_keep is not None:
        segs.append({
            "time_range": [round(current_start, 2), round(current_end, 2)],
            "action": "retain" if current_keep else "delete",
            "label": " + ".join(labels[:3]) or ("保留" if current_keep else "删除"),
        })
    return segs


async def _run_v6_export_task(project_id: str, with_subtitle: bool, segments_override: list[dict[str, Any]] | None, polish: dict[str, Any] | None = None) -> None:
    """后台执行 v6 导出。"""
    deps.set_task_status(project_id, "export", "running", progress=10)
    try:
        project = deps.load_project(project_id)
        video_path = project.video_path
        output_path = str(deps.get_exported_video_path(project_id))
        if polish:
            project.polish = polish
            deps.save_project(project_id, project)
        polish = polish or getattr(project, "polish", None) or {}

        # 优先使用前端传入的 transcript 句子状态，否则用项目当前 segments
        transcript = project.transcript or _load_transcript(project_id)
        if transcript and not segments_override:
            segments = _transcript_to_segments(transcript)
        else:
            segments = segments_override if segments_override else [
                {
                    "time_range": [seg.start_time, seg.end_time],
                    "action": "retain" if seg.keep else "delete",
                    "label": seg.label,
                }
                for seg in project.segments
            ]

        subtitle_srt: str | None = None
        pack = engine_v6._pack_from_transcript(transcript) if transcript else {}
        pack_path = deps._project_dir(project_id) / "pack.json"
        if pack.get("chapters"):
            import json
            pack_path.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
        elif pack_path.exists():
            import json
            try:
                pack = json.loads(pack_path.read_text(encoding="utf-8"))
            except Exception:
                pack = {}
        chapters = pack.get("chapters") or []
        retain = [s["time_range"] for s in segments if s.get("action") == "retain"]
        captions = polish.get("captions") or []
        if captions:
            subtitle_srt = str(deps._project_dir(project_id) / "subtitle.srt")
            engine_v6.generate_caption_srt(segments, captions, subtitle_srt)
        elif with_subtitle and transcript:
            subtitle_srt = str(deps._project_dir(project_id) / "subtitle.srt")
            engine_v6.generate_subtitle_srt(segments, transcript, subtitle_srt)

        if chapters and retain:
            from web.backend.render_douyin_pack import render_keep_pack
            deps.set_task_status(project_id, "export", "running", progress=40)
            render_keep_pack(video_path, retain, chapters, output_path)
            vf = engine_v6._polish_vf(polish, subtitle_srt if polish.get("caption_style") != "none" else None)
            if vf:
                tmp = output_path + ".p.mp4"
                proc = await asyncio.to_thread(
                    __import__("subprocess").run,
                    [engine_v6.FFMPEG, "-y", "-loglevel", "error", "-i", output_path, "-vf", vf,
                     "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-c:a", "aac", "-b:a", "128k", tmp],
                    capture_output=True, text=True, timeout=600,
                )
                if proc.returncode == 0:
                    os.replace(tmp, output_path)
            deps.set_task_status(
                project_id, "export", "done", progress=100,
                result={"output_path": output_path, "packed": True, "chapters": len(chapters)},
            )
            return

        deps.set_task_status(project_id, "export", "running", progress=40)
        result = await engine_v6.export(
            plan={"segments": segments},
            video_path=video_path,
            output_path=output_path,
            subtitle_srt=subtitle_srt,
            polish=polish,
        )

        deps.set_task_status(
            project_id,
            "export",
            "done",
            progress=100,
            result={
                "output_path": result["output_path"],
                "duration": result["duration"],
                "size_bytes": result["size_bytes"],
                "kept_segments": result["kept_segments"],
                "subtitle_mode": result["subtitle_mode"],
            },
        )
        logger.info("v6 导出完成: %s", result["output_path"])
    except Exception as exc:
        logger.error("v6 导出失败: %s", exc)
        deps.set_task_status(project_id, "export", "error", progress=0, error=str(exc))


async def _ensure_asr_ready():
    """云端 ASR 不需要本地 8123；仅 local 模式才检查。"""
    if engine_v6.asr_uses_cloud():
        if not engine_v6._read_dashscope_key():
            raise HTTPException(status_code=503, detail="未配置百炼 ASR Key（DASHSCOPE_API_KEY / BAILIAN_API_KEY）")
        return
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{engine_v6.ASR_URL}/health")
            if r.status_code != 200:
                raise HTTPException(status_code=503, detail="ASR 服务未就绪，请等待 start.sh 完成启动")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="ASR 服务未启动，请先运行 ./start.sh")


@router.post("/v6/analyze")
async def v6_analyze(
    project_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """启动 v6 两阶段 AI 分析后台任务。"""
    if not deps._project_dir(project_id).exists():
        raise HTTPException(status_code=404, detail="项目不存在")

    await _ensure_asr_ready()

    body = await request.json()
    user_prompt = body.get("user_prompt", "")
    platform = body.get("platform", "general")
    target_duration = body.get("target_duration")
    try:
        target_duration = float(target_duration) if target_duration not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        target_duration = None

    deps.set_task_status(project_id, "analyze", "pending", progress=0)
    background_tasks.add_task(_run_v6_analyze_task, project_id, user_prompt, platform, target_duration)
    return {"message": "v6 AI 分析已启动", "project_id": project_id}


@router.get("/v6/analyze/status")
async def v6_analyze_status(project_id: str) -> dict[str, Any]:
    """查询 v6 分析任务状态。"""
    status = deps.get_task_status(project_id)
    if status is None:
        return {"task": "analyze", "status": "idle", "progress": 0}
    return status


@router.post("/v6/export")
async def v6_export(
    project_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """启动 v6 视频导出后台任务。"""
    if not deps._project_dir(project_id).exists():
        raise HTTPException(status_code=404, detail="项目不存在")

    body = await request.json()
    with_subtitle = bool(body.get("with_subtitle", False))
    segments_override = body.get("segments", None)
    polish = body.get("polish") if isinstance(body.get("polish"), dict) else None

    deps.set_task_status(project_id, "export", "pending", progress=0)
    background_tasks.add_task(_run_v6_export_task, project_id, with_subtitle, segments_override, polish)
    return {"message": "v6 视频导出已启动", "project_id": project_id}


@router.get("/v6/export/status")
async def v6_export_status(project_id: str) -> dict[str, Any]:
    """查询 v6 导出任务状态。"""
    status = deps.get_task_status(project_id)
    if status is None:
        return {"task": "export", "status": "idle", "progress": 0}
    return status


@router.post("/v6/transcript/segments")
async def v6_transcript_segments(project_id: str, request: Request) -> dict[str, Any]:
    """根据 transcript 句子状态计算 segments，不触发导出。"""
    if not deps._project_dir(project_id).exists():
        raise HTTPException(status_code=404, detail="项目不存在")

    body = await request.json()
    transcript = body.get("transcript")
    if transcript is None:
        project = deps.load_project(project_id)
        transcript = project.transcript or _load_transcript(project_id)

    segments = _transcript_to_segments(transcript)
    retain_duration = sum(s["time_range"][1] - s["time_range"][0] for s in segments if s["action"] == "retain")
    return {
        "segments": segments,
        "retain_duration": round(retain_duration, 2),
        "retain_count": len([s for s in segments if s["action"] == "retain"]),
    }
