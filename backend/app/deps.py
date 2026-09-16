import hmac
from dataclasses import dataclass

from fastapi import Header, HTTPException, Query

from app.config import settings


@dataclass
class PageParams:
    page: int
    size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size


def pagination(page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100)) -> PageParams:
    return PageParams(page=page, size=size)


def require_auth(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> None:
    """可选 API Token 鉴权（全局依赖）。

    - 未配置 `API_AUTH_TOKEN` 时放行（本地单机开发保持开箱即用）。
    - 配置后，所有 /api 请求（health 除外）需携带 `Authorization: Bearer <token>`
      或 `X-API-Key: <token>`，用于对外/局域网暴露时防止未授权调用。

    用 hmac.compare_digest 做常量时间比较，避免时序侧信道。
    """
    token = settings.api_auth_token
    if not token:
        return
    provided = ""
    if authorization and authorization.startswith("Bearer "):
        provided = authorization[7:].strip()
    elif x_api_key:
        provided = x_api_key.strip()
    if not provided or not hmac.compare_digest(provided, token):
        raise HTTPException(status_code=401, detail="未授权：缺少或错误的 API Token")
