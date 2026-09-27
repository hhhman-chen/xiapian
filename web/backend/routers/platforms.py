"""平台偏好路由"""
from fastapi import APIRouter
from web.backend.platforms import list_platforms, PLATFORM_PROFILES

router = APIRouter()


@router.get("/", summary="获取平台列表")
async def get_platforms():
    """返回所有支持的平台 [{id, label}]"""
    return list_platforms()


@router.get("/{platform_id}", summary="获取某平台偏好")
async def get_platform_profile(platform_id: str):
    """返回某平台的完整偏好 profile"""
    if platform_id not in PLATFORM_PROFILES:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="平台不存在")
    return PLATFORM_PROFILES[platform_id]