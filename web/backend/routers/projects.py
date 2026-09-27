"""
项目 CRUD 路由。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

import editor_core
from models import Project
from web.backend import dependencies as deps

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("/")
async def list_projects(request: Request) -> dict[str, Any]:
    """列出当前用户的项目。管理员可见全部。"""
    user = getattr(request.state, "user", None) or {}
    username = user.get("u") or ""
    role = user.get("r") or "trial"
    items: list[dict[str, Any]] = []
    for pid in deps.list_project_ids():
        try:
            project = deps.load_project(pid)
            owner = getattr(project, "owner", "") or ""
            if role != "admin" and owner != username:
                continue
            items.append({
                "id": pid,
                "video_path": project.video_path,
                "duration": project.duration,
                "fps": project.fps,
                "width": project.width,
                "height": project.height,
                "segments_count": len(project.segments),
                "owner": owner,
            })
        except Exception as exc:
            logger.warning("读取项目 %s 失败: %s", pid, exc)
    return {"projects": items}


def _concat_videos(paths: list[Path], dest: Path) -> None:
    """多段毛片拼成一条时间线，客户素材不必先手工合并。"""
    import subprocess
    import tempfile
    ffmpeg = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
    if not Path(ffmpeg).exists():
        from shutil import which
        ffmpeg = which("ffmpeg") or "ffmpeg"
    lst = dest.with_suffix(".concat.txt")
    normalized = []
    tmp = Path(tempfile.mkdtemp(prefix="up_concat_"))
    try:
        for i, p in enumerate(paths):
            out = tmp / f"n{i:02d}.mp4"
            subprocess.run(
                [ffmpeg, "-y", "-loglevel", "error", "-i", str(p),
                 "-vf", "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2",
                 "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                 "-c:a", "aac", "-ar", "48000", "-ac", "1", str(out)],
                check=True, capture_output=True, text=True, timeout=300,
            )
            normalized.append(out)
        lst.write_text("".join(f"file '{n}'\n" for n in normalized), encoding="utf-8")
        subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
             "-c", "copy", str(dest)],
            check=True, capture_output=True, text=True, timeout=120,
        )
    finally:
        for n in normalized:
            try:
                n.unlink()
            except OSError:
                pass


def _upload_dir(upload_id: str) -> Path:
    d = deps.DATA_DIR / "_uploads" / upload_id
    if not d.exists():
        raise HTTPException(status_code=404, detail="上传会话不存在")
    return d


@router.post("/upload/init")
async def upload_init(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    """分片上传：Cloudflare 免费档单次请求上限 100MB，大文件必须拆开。"""
    uid = deps.uuid.uuid4().hex
    d = deps.DATA_DIR / "_uploads" / uid
    d.mkdir(parents=True, exist_ok=True)
    meta = {
        "filename": payload.get("filename") or "clip.mp4",
        "size": int(payload.get("size") or 0),
        "chunks": int(payload.get("chunks") or 0),
        "owner": (getattr(request.state, "user", None) or {}).get("u") or "",
    }
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return {"upload_id": uid}


@router.post("/upload/chunk")
async def upload_chunk(
    upload_id: str = Form(...),
    index: int = Form(...),
    chunk: UploadFile = File(...),
) -> dict[str, Any]:
    d = _upload_dir(upload_id)
    if index < 0 or index > 20000:
        raise HTTPException(status_code=400, detail="分片序号无效")
    dest = d / f"{index:05d}.part"
    dest.write_bytes(await chunk.read())
    return {"ok": True, "index": index}


@router.post("/upload/finish")
async def upload_finish(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    """拼分片、可选多段合并、创建项目。"""
    ids = payload.get("upload_ids") or []
    if payload.get("upload_id"):
        ids = [payload["upload_id"]] + list(ids)
    ids = [str(x) for x in ids if x]
    if not ids:
        raise HTTPException(status_code=400, detail="缺少 upload_id")
    owner = (getattr(request.state, "user", None) or {}).get("u") or ""
    assembled: list[Path] = []
    try:
        for uid in ids:
            d = _upload_dir(uid)
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
            parts = sorted(d.glob("*.part"))
            if not parts:
                raise HTTPException(status_code=400, detail="没有收到分片")
            suffix = Path(meta.get("filename") or "clip.mp4").suffix or ".mp4"
            out = d / f"assembled{suffix}"
            with out.open("wb") as w:
                for p in parts:
                    w.write(p.read_bytes())
                    p.unlink()
            assembled.append(out)
        original_name = payload.get("name") or "upload.mp4"
        if len(assembled) == 1:
            project_id = deps.create_project(assembled[0], original_name, owner=owner)
        else:
            merged = deps.DATA_DIR / f"tmp_merge_{deps.uuid.uuid4().hex}.mp4"
            _concat_videos(assembled, merged)
            project_id = deps.create_project(merged, original_name, owner=owner)
            if merged.exists():
                merged.unlink()
        return {"id": project_id, "message": "项目创建成功", "clips": len(assembled)}
    finally:
        import shutil
        for uid in ids:
            d = deps.DATA_DIR / "_uploads" / uid
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)


@router.post("/upload")
async def upload_video(
    request: Request,
    file: Optional[UploadFile] = File(None),
    files: Optional[list[UploadFile]] = File(None),
    name: Optional[str] = Form(None),
) -> dict[str, Any]:
    """上传一段或多段素材并创建项目。多段会按选择顺序拼成一条时间线。"""
    uploads = [u for u in (files or []) if u and u.filename]
    if not uploads and file and file.filename:
        uploads = [file]
    if not uploads:
        raise HTTPException(status_code=400, detail="缺少文件")

    tmp_paths: list[Path] = []
    try:
        for u in uploads:
            suffix = Path(u.filename or "clip.mp4").suffix or ".mp4"
            tmp_path = deps.DATA_DIR / f"tmp_upload_{deps.uuid.uuid4().hex}{suffix}"
            tmp_path.write_bytes(await u.read())
            tmp_paths.append(tmp_path)
        original_name = name or uploads[0].filename or "upload.mp4"
        owner = (getattr(request.state, "user", None) or {}).get("u") or ""
        if len(tmp_paths) == 1:
            project_id = deps.create_project(tmp_paths[0], original_name, owner=owner)
        else:
            merged = deps.DATA_DIR / f"tmp_merge_{deps.uuid.uuid4().hex}.mp4"
            _concat_videos(tmp_paths, merged)
            project_id = deps.create_project(merged, original_name, owner=owner)
            if merged.exists():
                merged.unlink()
    finally:
        for p in tmp_paths:
            if p.exists():
                p.unlink()

    return {"id": project_id, "message": "项目创建成功", "clips": len(uploads)}


@router.get("/{project_id}")
async def get_project(project_id: str) -> dict[str, Any]:
    """获取项目完整 JSON。"""
    try:
        project = deps.load_project(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return project.to_dict()


@router.post("/{project_id}")
async def save_project(project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """保存项目 JSON。"""
    try:
        project = Project.from_dict(payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"项目数据无效: {exc}")

    if not deps._project_dir(project_id).exists():
        raise HTTPException(status_code=404, detail="项目不存在")

    deps.save_project(project_id, project)
    return {"id": project_id, "message": "项目保存成功"}


@router.delete("/{project_id}")
async def delete_project(project_id: str) -> dict[str, Any]:
    """删除项目。"""
    if not deps.delete_project(project_id):
        raise HTTPException(status_code=404, detail="项目不存在")
    return {"id": project_id, "message": "项目已删除"}
