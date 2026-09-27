"""命令行跑一条视频：分析 + 导出。验收用，不依赖 Web UI。

用法：
  cd /Users/chenkangye/workspace/migrated/video-editor
  python3 -m web.backend.run_cut \\
    --video ~/.openclaw/workspace/obsidian-vault-video-editor/赵湄原片.mp4 \\
    --target 153 --platform general --export
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# 保证以项目根为 cwd 时能 import web.backend
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from web.backend import engine_v6  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="v6/v7 智能剪辑命令行")
    parser.add_argument("--video", required=True, help="原片路径")
    parser.add_argument("--target", type=float, default=153, help="目标成片秒数，0 表示不强制")
    parser.add_argument("--platform", default="general")
    parser.add_argument("--prompt", default="删掉口头禅、停顿、导师插话和重复，保留孩子完整讲书的叙事和金句，剪成流畅成片")
    parser.add_argument("--export", action="store_true", help="分析后立刻导出 mp4")
    parser.add_argument("--subtitle", action="store_true")
    parser.add_argument("--out-dir", default="", help="输出目录，默认 output/<stem>_cut/")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    video = os.path.abspath(os.path.expanduser(args.video))
    if not os.path.exists(video):
        raise SystemExit(f"视频不存在: {video}")

    stem = Path(video).stem
    out_dir = Path(args.out_dir) if args.out_dir else ROOT / "output" / f"{stem}_cut"
    out_dir.mkdir(parents=True, exist_ok=True)

    target = args.target if args.target and args.target > 0 else None
    asyncio.run(_run(video, target, args.platform, args.prompt, args.export, args.subtitle, out_dir))


async def _run(video: str, target, platform: str, prompt: str, do_export: bool, subtitle: bool, out_dir: Path) -> None:
    print(f"[run_cut] video={video}")
    print(f"[run_cut] target={target}s platform={platform}")
    result = await engine_v6.analyze(
        video_path=video,
        user_prompt=prompt,
        platform=platform,
        target_duration=target,
    )
    result_path = out_dir / "analyze.json"
    # visual_content 可能很长，落盘完整结果
    slim = dict(result)
    if isinstance(slim.get("visual_understanding"), dict):
        vis = dict(slim["visual_understanding"])
        if vis.get("visual_content") and len(vis["visual_content"]) > 8000:
            vis["visual_content"] = vis["visual_content"][:8000] + "…"
        slim["visual_understanding"] = vis
    result_path.write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")

    stats = result.get("statistics", {})
    print("—— 分析结果 ——")
    print(f"  原片 {stats.get('total_duration')}s → 成片 {stats.get('retained_duration')}s")
    print(f"  片段 {stats.get('retained_segments')}/{stats.get('total_segments')} 保留")
    print(f"  一轮 {stats.get('round1_duration')}s  二轮 {stats.get('round2_applied')}")
    print(f"  耗时 {result.get('elapsed_s')}s  timings={result.get('timings')}")
    print(f"  模型 {result.get('model')}")
    print(f"  摘要 {result.get('summary', '')[:120]}")
    print(f"  已写 {result_path}")

    if not do_export:
        return

    output_mp4 = str(out_dir / "final.mp4")
    srt_path = None
    if subtitle and result.get("transcript"):
        srt_path = str(out_dir / "subtitle.srt")
        engine_v6.generate_subtitle_srt(result.get("suggestions", []), result["transcript"], srt_path)

    exported = await engine_v6.export(
        plan={"segments": result.get("suggestions", [])},
        video_path=video,
        output_path=output_mp4,
        subtitle_srt=srt_path,
    )
    print("—— 导出 ——")
    print(json.dumps(exported, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
