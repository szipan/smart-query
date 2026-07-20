"""knowledge-admin 数据模型与校验（design.md §3）。

- GlossaryTerm（软通道）：term / synonyms / definition / enabled ...
- EntityEntry（硬通道）：canonical / entity_type / entity_id / aliases / enabled ...
- normalize()：统一归一化（供唯一性判断与硬通道匹配共享，两侧必须用同一份）。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Optional

from knowledge_admin.errors import ValidationError


def normalize(text: str) -> str:
    """归一化：NFKC（全角→半角等）+ 去首尾空白 + 折叠内部空白 + 小写。

    软通道 term 唯一性、硬通道别名唯一性与运行时匹配，全部共用本函数，
    保证「写入归一化」与「匹配归一化」一致（Req 11.5）。
    """
    if text is None:
        return ""
    t = unicodedata.normalize("NFKC", str(text)).strip().lower()
    return " ".join(t.split())


def _clean_list(items: Optional[list]) -> list[str]:
    """去空白、丢空串、按归一化去重（保留原文首次出现形态）。"""
    out: list[str] = []
    seen: set[str] = set()
    for it in items or []:
        s = str(it).strip()
        if not s:
            continue
        key = normalize(s)
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


@dataclass
class GlossaryTerm:
    """软通道术语条目。Business_Key = term（归一化后唯一）。"""

    term: str
    synonyms: list[str] = field(default_factory=list)
    definition: str = ""
    enabled: bool = True
    term_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def validate(self) -> "GlossaryTerm":
        self.term = (self.term or "").strip()
        if not self.term:
            raise ValidationError("term 不能为空")
        self.synonyms = _clean_list(self.synonyms)
        self.definition = (self.definition or "").strip()
        return self

    @property
    def business_key(self) -> str:
        return normalize(self.term)

    def index_text(self) -> str:
        """写入向量库时被向量化的文本（term + 同义词 + 定义）。"""
        return " ".join([self.term, *self.synonyms, self.definition]).strip()


@dataclass
class EntityEntry:
    """硬通道实体条目。Business_Key = (entity_type, entity_id)；别名跨实体全局唯一。"""

    canonical: str
    entity_type: str
    entity_id: str
    aliases: list[str] = field(default_factory=list)
    enabled: bool = True
    entry_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def validate(self) -> "EntityEntry":
        self.canonical = (self.canonical or "").strip()
        self.entity_type = (self.entity_type or "").strip()
        self.entity_id = (self.entity_id or "").strip()
        if not self.canonical:
            raise ValidationError("canonical 不能为空")
        if not self.entity_type:
            raise ValidationError("entity_type 不能为空")
        if not self.entity_id:
            raise ValidationError("entity_id 不能为空")
        self.aliases = _clean_list(self.aliases)
        # 别名不得与自身 canonical 归一化后相同（避免自指冲突）
        canon_key = normalize(self.canonical)
        self.aliases = [a for a in self.aliases if normalize(a) != canon_key]
        return self

    @property
    def business_key(self) -> str:
        return f"{self.entity_type}#{self.entity_id}"

    def alias_keys(self) -> list[str]:
        """归一化后的别名键（供唯一性判断与匹配加载）。"""
        return [normalize(a) for a in self.aliases]
