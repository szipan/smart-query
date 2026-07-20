"""AuthGuard 占位 + AdminObserver 单测。"""

from __future__ import annotations

import pytest

from knowledge_admin.auth import PlaceholderAuthGuard
from knowledge_admin.errors import Forbidden, Unauthorized
from knowledge_admin.observer import InMemoryAdminObserver, NullAdminObserver


# ---- AuthGuard 占位（默认放行）----
def test_placeholder_allows_and_parses_actor() -> None:
    g = PlaceholderAuthGuard()
    assert g.require_admin("Bearer alice").actor_id == "alice"
    assert g.require_admin(None).actor_id == "placeholder-admin"


# ---- AuthGuard 配置 token（最简校验）----
def test_token_mode_accepts_correct_token() -> None:
    g = PlaceholderAuthGuard(admin_token="s3cret")
    assert g.require_admin("Bearer s3cret").actor_id == "admin"


def test_token_mode_missing_header_unauthorized() -> None:
    g = PlaceholderAuthGuard(admin_token="s3cret")
    with pytest.raises(Unauthorized):
        g.require_admin(None)


def test_token_mode_wrong_token_forbidden() -> None:
    g = PlaceholderAuthGuard(admin_token="s3cret")
    with pytest.raises(Forbidden):
        g.require_admin("Bearer wrong")


# ---- Observer ----
def test_inmemory_observer_records_metadata_only() -> None:
    obs = InMemoryAdminObserver()
    obs.record(operation="glossary.create", business_key="发电量", actor="alice",
               extra={"token": "should-not-be-stored"})
    rec = obs.records[0]
    assert rec["operation"] == "glossary.create" and rec["actor"] == "alice"
    assert "at" in rec
    assert "token" not in rec   # 脱敏：不落 extra 敏感字段


def test_null_observer_noop() -> None:
    assert NullAdminObserver().record(operation="x", business_key="k", actor="a") is None
