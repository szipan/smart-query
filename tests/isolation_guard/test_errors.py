"""Task 2 单测：异常层次（Requirements 12.1）。"""

import pytest

from isolation_guard.errors import (
    ConfigurationError,
    IsolationGuardError,
    PermissionResolutionError,
    ReadOnlyViolation,
    UnmappedControlledTableError,
)


@pytest.mark.parametrize(
    "exc",
    [
        ReadOnlyViolation("x"),
        PermissionResolutionError("x"),
        ConfigurationError("x"),
        UnmappedControlledTableError("gen"),
    ],
)
def test_all_exceptions_are_subclass_of_base(exc: Exception) -> None:
    assert isinstance(exc, IsolationGuardError)


def test_unmapped_controlled_table_carries_table_name() -> None:
    err = UnmappedControlledTableError("bus_points_day")
    assert err.table_name == "bus_points_day"
    assert "bus_points_day" in str(err)


def test_base_catches_all() -> None:
    try:
        raise UnmappedControlledTableError("t")
    except IsolationGuardError as e:
        assert isinstance(e, UnmappedControlledTableError)
