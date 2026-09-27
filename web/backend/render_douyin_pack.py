"""把毛片剪成抖音口播包装：黑底、圆头像、黄字要点、竖屏 720x1280。

参考成片结构（粤语托管口播）：
  钩子（现场）→ 房东故事 → 第一…第五 服务要点 → 关注 CTA
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

def _find_ffmpeg() -> str:
    for c in (
        os.environ.get("FFMPEG"),
        "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
        "/opt/homebrew/bin/ffmpeg",
        "ffmpeg",
    ):
        if c and (c == "ffmpeg" or os.path.exists(c)):
            return c
    return "ffmpeg"


FFMPEG = _find_ffmpeg()
def _find_font() -> str:
    for c in (
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ):
        if os.path.exists(c):
            return c
    return "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"


FONT = _find_font()

W, H = 720, 1280
YELLOW = "0xFFD400"


def _run(cmd: list, timeout: int = 180) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-500:] if proc.stderr else "ffmpeg failed")


def cut_clip(src: str, start: float, end: float, dst: str) -> None:
    dur = max(0.2, end - start)
    _run([
        FFMPEG, "-y", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", src,
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "1",
        dst,
    ])


def full_frame(src: str, dst: str) -> None:
    """竖屏铺满。"""
    _run([
        FFMPEG, "-y", "-loglevel", "error", "-i", src,
        "-vf", (
            f"scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},eq=contrast=1.05:saturation=1.08"
        ),
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "1",
        dst,
    ])


def point_card(src: str, title: str, subtitle: str, dst: str) -> None:
    """黑底 + 圆头像 + 黄色大标题（对标成片『第一 / 租金水电催缴』）。"""
    safe_title = title.replace("'", "’")[:18]
    safe_sub = subtitle.replace("'", "’")[:22]
    font = FONT.replace("\\", "\\\\").replace(":", "\\:")
    vf = (
        f"scale=520:520:force_original_aspect_ratio=increase,crop=520:520,"
        f"format=rgba,"
        f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
        f"a='if(lte(hypot(X-W/2,Y-H/2),250),255,0)',"
        f"pad={W}:{H}:(ow-iw)/2:90:black,"
        f"drawtext=fontfile='{font}':text='{safe_title}':fontsize=72:fontcolor={YELLOW}:"
        f"x=(w-text_w)/2:y=660:borderw=2:bordercolor=black,"
        f"drawtext=fontfile='{font}':text='{safe_sub}':fontsize=36:fontcolor=white:"
        f"x=(w-text_w)/2:y=760"
    )
    _run([
        FFMPEG, "-y", "-loglevel", "error", "-i", src,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "1",
        "-pix_fmt", "yuv420p",
        dst,
    ])


def concat_list(clips: list, dst: str) -> None:
    lst = dst + ".txt"
    with open(lst, "w") as f:
        for c in clips:
            f.write(f"file '{os.path.abspath(c)}'\n")
    _run([
        FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst,
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "1",
        "-movflags", "+faststart",
        dst,
    ])


def render_keep_pack(
    src: str,
    retain_ranges: list,
    chapters: list,
    out: str,
) -> str:
    """任意新素材：保留区间 + 章节卡。chapters: {start,end,index,title} 用源时间轴。"""
    tmp = tempfile.mkdtemp(prefix="dy_keep_")
    pieces = []
    for i, rng in enumerate(retain_ranges):
        a, b = float(rng[0]), float(rng[1])
        if b - a < 0.15:
            continue
        raw = os.path.join(tmp, f"r{i}_raw.mp4")
        cut_clip(src, a, b, raw)
        ch = None
        for c in chapters or []:
            try:
                c0, c1 = float(c.get("start", 0)), float(c.get("end", 0))
            except (TypeError, ValueError):
                continue
            ov = max(0.0, min(b, c1) - max(a, c0))
            if ov >= 0.45 * (b - a):
                ch = c
                break
        dst = os.path.join(tmp, f"r{i}.mp4")
        if ch:
            point_card(raw, str(ch.get("index") or ch.get("title") or ""), str(ch.get("title") or ""), dst)
        else:
            full_frame(raw, dst)
        pieces.append(dst)
    if not pieces:
        raise RuntimeError("没有可导出的保留片段")
    concat_list(pieces, out)
    return out


def render(src1: str, src2: str, src3: str, out: str) -> str:
    """src2=钩子现场, src1=房东故事, src3=五点服务。"""
    tmp = tempfile.mkdtemp(prefix="dy_pack_")
    pieces = []

    # 钩子：2号「喂翻来睇现场」，去掉「二、开始」
    hook_raw = os.path.join(tmp, "hook_raw.mp4")
    hook = os.path.join(tmp, "hook.mp4")
    cut_clip(src2, 4.15, 8.20, hook_raw)
    full_frame(hook_raw, hook)
    pieces.append(hook)

    # 房东故事
    story_raw = os.path.join(tmp, "story_raw.mp4")
    story = os.path.join(tmp, "story.mp4")
    cut_clip(src1, 2.50, 18.70, story_raw)
    full_frame(story_raw, story)
    pieces.append(story)

    # 列表导语
    intro_raw = os.path.join(tmp, "intro_raw.mp4")
    intro = os.path.join(tmp, "intro.mp4")
    cut_clip(src3, 2.20, 6.70, intro_raw)
    full_frame(intro_raw, intro)
    pieces.append(intro)

    points = [
        (8.10, 13.40, "第一", "公共卫生 每周一次"),
        (13.40, 20.80, "第二", "消防电器 定时检查"),
        (14.10, 20.80, "第三", "租金水电 催缴收缴"),
        (20.80, 23.20, "第四", "招租退租 全程代办"),
        (26.10, 35.30, "第五", "电器维修 下水道疏通"),
    ]
    # 第二句 ASR 只有 0.7s，用 13.4-14.1 太短；第二并进「定时检查」到第三之前。
    # 上面第三从 14.1 开始会与第二重叠。改成互斥区间：
    points = [
        (8.10, 13.40, "第一", "公共卫生 每周一次"),
        (13.40, 20.80, "第二", "消防电器 定时检查"),
        (20.80, 23.20, "第四", "招租退租 全程代办"),
        (26.10, 35.30, "第五", "电器维修 下水道疏通"),
    ]
    # 第三（租金水电）在 ASR 里是 14.1-20.8，和第二挤在一起。
    # 按成片：第二短、第三独立。从 13.4-20.8 里切开：第二约前 2s。
    points = [
        (8.10, 13.40, "第一", "公共卫生 每周一次"),
        (13.40, 15.60, "第二", "消防电器 定时检查"),
        (15.60, 20.80, "第三", "租金水电 催缴收缴"),
        (20.80, 23.20, "第四", "招租退租 全程代办"),
        (26.10, 35.30, "第五", "电器维修 下水道疏通"),
    ]

    for i, (a, b, title, sub) in enumerate(points):
        raw = os.path.join(tmp, f"p{i}_raw.mp4")
        card = os.path.join(tmp, f"p{i}.mp4")
        cut_clip(src3, a, b, raw)
        point_card(raw, title, sub, card)
        pieces.append(card)

    cta_raw = os.path.join(tmp, "cta_raw.mp4")
    cta = os.path.join(tmp, "cta.mp4")
    cut_clip(src3, 35.30, 40.30, cta_raw)
    full_frame(cta_raw, cta)
    pieces.append(cta)

    concat_list(pieces, out)
    return out


if __name__ == "__main__":
    import sys
    a, b, c, o = sys.argv[1:5]
    print(render(a, b, c, o))
