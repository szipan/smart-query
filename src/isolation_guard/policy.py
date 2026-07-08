"""表隔离策略与列绑定（design.md §4.2 + §5A.2）。

受控表不再是"表→单一列名"，而是"表→ColumnBinding"：
- dimension：PS / ORG（决定用可见 ps_id 源还是可见 org_id 源）
- column：物理列名
- transform：列取值如何映射到维度 id
  - IDENTITY：列值即维度 id（ps_id 列、org_id 列）
  - PS_ID_FROM_PS_KEY：列是 ps_key，取 split_part(col,'_',1) 得 ps_id
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from isolation_guard.models import ScopeDim


class ColTransform(str, Enum):
    IDENTITY = "identity"
    PS_ID_FROM_PS_KEY = "ps_from_key"


@dataclass(frozen=True)
class ColumnBinding:
    """受控表的隔离列绑定。"""

    dimension: ScopeDim
    column: str
    transform: ColTransform = ColTransform.IDENTITY


class UnknownTablePolicy(str, Enum):
    """对既非受控、也非非受控白名单的"未知表"如何处理。"""

    FAIL_CLOSED = "fail_closed"      # 默认（推荐）：视为"必控却未映射"，拒绝
    PASS_THROUGH = "pass_through"    # 仅在明确评估安全后开启（不推荐）


@dataclass(frozen=True)
class TableClass:
    """表分类结果。"""

    kind: str                              # "controlled" | "uncontrolled" | "must_control_unmapped"
    binding: Optional[ColumnBinding] = None


@dataclass(frozen=True)
class TablePolicy:
    """表→隔离绑定映射（可维护配置，可从配置中心/DynamoDB 加载）。"""

    controlled: dict[str, ColumnBinding]
    uncontrolled: frozenset[str] = frozenset()
    unknown_table_policy: UnknownTablePolicy = UnknownTablePolicy.FAIL_CLOSED
    case_insensitive: bool = True

    def _key(self, name: str) -> str:
        return name.lower() if self.case_insensitive else name

    def classify(self, table_name: str) -> TableClass:
        name = self._key(table_name)
        controlled = {self._key(k): v for k, v in self.controlled.items()}
        uncontrolled = {self._key(t) for t in self.uncontrolled}

        if name in controlled:
            return TableClass(kind="controlled", binding=controlled[name])
        if name in uncontrolled:
            return TableClass(kind="uncontrolled")
        if self.unknown_table_policy is UnknownTablePolicy.PASS_THROUGH:
            return TableClass(kind="uncontrolled")
        return TableClass(kind="must_control_unmapped")
