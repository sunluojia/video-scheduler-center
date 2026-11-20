"""账号池：爬取程序随心跳报来的身份池快照，见爬取方案第 6.3 节。

身份是未登录的浏览器访客，不是平台账号；Cookie 和 token 只在设备上，不进快照。
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class IdentityState(StrEnum):
    READY = "ready"
    IN_USE = "in_use"
    WARMING = "warming"
    COOLING = "cooling"
    STALE = "stale"
    """token 过期或还没预热，等后台循环预热。"""
    RETIRED = "retired"


class IdentityAction(StrEnum):
    REFRESH = "refresh"
    RETIRE = "retire"


class Identity(BaseModel):
    id: str = Field(max_length=64)
    """资料目录名。"""
    state: IdentityState
    fails: int
    """连续失败次数，只有下载被拒才算。"""
    cooldown_until: datetime | None = None
    token_expires_at: datetime | None = None
    last_used_at: datetime | None = None
    warmed_at: datetime | None = None
    browser: str | None = Field(None, max_length=32)
    retired_at: datetime | None = None
    retire_reason: str | None = Field(None, max_length=200)


class IdentitySnapshot(BaseModel):
    target: int
    """这台设备分到的可用数量。"""
    ready: int
    """可用：有没过期的 token、不在冷却，正在用的也算。"""
    in_use: int
    warming: int
    cooling: int
    total: int
    short_since: datetime | None = None
    """可用数从什么时候开始少于目标。"""
    warm_total: int = 0
    """以下都是最近一小时：预热次数和失败次数、作品列表请求数和空内容数、下载次数和被拒（403）数。"""
    warm_failed: int = 0
    list_total: int = 0
    list_empty: int = 0
    download_total: int = 0
    download_blocked: int = 0
    items: list[Identity] = Field(max_length=200)
    """在池里的身份，加上最近 24 小时退休的（最多 20 个）。"""


class IdentityDevice(BaseModel):
    instance_id: str
    online: bool
    last_seen_at: datetime
    snapshot: IdentitySnapshot
    """设备离线后仍是它最后报来的样子。"""


class IdentityPoolSetting(BaseModel):
    target: int = Field(ge=1, le=200)
    """账号池至少保持几个可用身份。爬取程序按心跳分到的数量自动补充、刷新和替换。"""


class IdentityBoard(BaseModel):
    target: int
    """账号池至少保持几个可用身份，在线的爬取程序平均分摊。"""
    devices: list[IdentityDevice]
    alerts: list[str]
    """需要人处理的问题。"""
