"""出站 httpx 客户端：忽略系统代理，不校验证书。"""

from __future__ import annotations

from typing import Any

import httpx

POLICY: dict[str, Any] = {"trust_env": False, "verify": False}


def make_async_client(**kwargs: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(**{**kwargs, **POLICY})


def make_sync_client(**kwargs: Any) -> httpx.Client:
    return httpx.Client(**{**kwargs, **POLICY})


def mcp_client_factory(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
    kwargs: dict[str, Any] = {"follow_redirects": True}
    if headers is not None:
        kwargs["headers"] = headers
    if timeout is not None:
        kwargs["timeout"] = timeout
    if auth is not None:
        kwargs["auth"] = auth
    return make_async_client(**kwargs)
