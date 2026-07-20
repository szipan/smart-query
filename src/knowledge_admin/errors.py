"""knowledge-admin 错误类型（映射 HTTP 状态；见 design.md §9）。"""

from __future__ import annotations

from typing import Any, Optional


class AdminError(Exception):
    """管理面错误基类。code 用于响应信封，http_status 用于 API 层。"""

    code: str = "ADMIN_ERROR"
    http_status: int = 400

    def __init__(self, message: str, detail: Optional[Any] = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


class ValidationError(AdminError):
    code = "VALIDATION_ERROR"
    http_status = 400


class DuplicateKeyError(AdminError):
    code = "DUPLICATE_KEY"
    http_status = 409


class AliasConflictError(AdminError):
    code = "ALIAS_CONFLICT"
    http_status = 409


class NotFoundError(AdminError):
    code = "NOT_FOUND"
    http_status = 404


class Unauthorized(AdminError):
    code = "UNAUTHORIZED"
    http_status = 401


class Forbidden(AdminError):
    code = "FORBIDDEN"
    http_status = 403


class IndexSyncError(AdminError):
    code = "INDEX_SYNC_ERROR"
    http_status = 502
