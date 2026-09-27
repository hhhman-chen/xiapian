"""
平台偏好库 — 各发布平台的剪辑偏好

驱动阶段2 K3 决策：根据用户选的平台自动套用时长/画幅/节奏偏好
"""

PLATFORM_PROFILES = {
    "douyin": {
        "label": "抖音",
        "target_duration": "15-60秒（短视频）",
        "aspect_ratio": "9:16 竖屏",
        "rhythm": "快节奏，前3秒必须抓人，平均镜头 1.5-3秒",
        "structure": "hook(3s) → 主体 → 高潮 → CTA/反转",
        "rules": [
            "前3秒必须有强画面或金句抓人",
            "成片控制在 60 秒内，宁可短不可长",
            "节奏要快，长段落拆短，无停顿",
            "适合竖屏 9:16，画面主体居中",
            "结尾留钩子（金句/反问）促评论转发",
        ],
    },
    "bilibili": {
        "label": "B站",
        "target_duration": "3-10分钟（中长视频）",
        "aspect_ratio": "16:9 横屏为主",
        "rhythm": "中等节奏，叙事完整，平均镜头 3-8秒",
        "structure": "开场介绍 → 主体分章节 → 总结/互动",
        "rules": [
            "时长可以长一些，3-10分钟都行",
            "叙事要完整，不要碎片化",
            "横屏 16:9 为主",
            "结尾可以加互动引导（三连/关注）",
            "口癖/停顿该删就删，但保留说话人的真实感",
        ],
    },
    "youtube": {
        "label": "YouTube",
        "target_duration": "5-15分钟（中长视频）",
        "aspect_ratio": "16:9 横屏",
        "rhythm": "中等节奏，开场8秒抓人，平均镜头 3-6秒",
        "structure": "hook(8s) → intro → 主体 → 总结 → CTA",
        "rules": [
            "前 8 秒要抓人，否则观众跳出",
            "时长 5-15 分钟，叙事完整",
            "横屏 16:9",
            "口癖/长停顿必须删",
            "结尾留 CTA（订阅/点赞）",
        ],
    },
    "xiaohongshu": {
        "label": "小红书",
        "target_duration": "30-90秒",
        "aspect_ratio": "9:16 或 3:4 竖屏",
        "rhythm": "中等偏快，画面要美，平均镜头 2-4秒",
        "structure": "封面 hook → 主体 → 福利/总结",
        "rules": [
            "时长 30-90 秒，紧凑",
            "竖屏 9:16 或 3:4",
            "画面要精致，构图美观",
            "适合加文字标题贴纸",
            "结尾给干货总结或关注看更多",
        ],
    },
    "general": {
        "label": "通用",
        "target_duration": "由内容决定，紧凑流畅即可",
        "aspect_ratio": "保持原片",
        "rhythm": "自然流畅，该删的删",
        "structure": "保留有效内容，叙事连贯",
        "rules": [
            "删除口癖/停顿/重复/画外音/口误",
            "保留有效内容，叙事连贯",
            "成片要紧凑但不为短而短",
            "保持原片画幅",
        ],
    },
}


def get_profile(platform: str) -> dict:
    """获取平台偏好，未知平台降级到 general"""
    return PLATFORM_PROFILES.get(platform, PLATFORM_PROFILES["general"])


def list_platforms() -> list:
    """前端下拉用：返回 [{id, label}]"""
    return [{"id": k, "label": v["label"]} for k, v in PLATFORM_PROFILES.items()]