"""管理面可观测 / 审计（design.md §9）。

记录管理员写操作（操作类型 / 业务键 / 操作者 / 时间）。日志脱敏：只落上述结构化字段，
不落敏感明文凭证。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("knowledge_admin.audit")


class NullAdminObserver:
    def record(self, *, operation: str, business_key: str, actor: str,
               extra: Optional[dict] = None) -> None:
        return None


class InMemoryAdminObserver:
    """内存审计（测试 / 小规模）。"""

    def __init__(self) -> None:
        self.records: list[dict] = []

    def record(self, *, operation: str, business_key: str, actor: str,
               extra: Optional[dict] = None) -> None:
        self.records.append({
            "operation": operation, "business_key": business_key, "actor": actor,
            "at": datetime.now(timezone.utc).isoformat(),
        })


class LoggingAdminObserver:
    """结构化日志审计。只记录操作元数据，不记录凭证等敏感明文。"""

    def record(self, *, operation: str, business_key: str, actor: str,
               extra: Optional[dict] = None) -> None:
        logger.info("admin_audit", extra={"operation": operation,
                                           "business_key": business_key, "actor": actor})
