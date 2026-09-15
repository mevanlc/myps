from unittest.mock import Mock

import psutil
import pytest

from myps import pssafe


@pytest.mark.parametrize(
    ("error_type", "sentinel"),
    [
        (psutil.NoSuchProcess, pssafe.NSP_NUMERIC),
        (psutil.ZombieProcess, pssafe.ZP_NUMERIC),
        (psutil.AccessDenied, pssafe.AD_NUMERIC),
    ],
)
def test_failed_ppid_is_never_used_to_construct_process(
    error_type, sentinel, monkeypatch
):
    proc = Mock(spec=psutil.Process)
    proc.ppid.side_effect = error_type(123)
    constructor = Mock()
    monkeypatch.setattr(psutil, "Process", constructor)

    ppid = pssafe.safe_get_ppid(proc)
    assert ppid == sentinel
    assert pssafe.safe_get_process(ppid) is None
    constructor.assert_not_called()


@pytest.mark.parametrize(
    "error_type", [psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied]
)
def test_expected_process_lookup_failure_returns_none(error_type, monkeypatch):
    constructor = Mock(side_effect=error_type(123))
    monkeypatch.setattr(psutil, "Process", constructor)

    assert pssafe.safe_get_process(123) is None
    constructor.assert_called_once_with(123)


def test_process_lookup_preserves_valid_pids_including_zero(monkeypatch):
    constructor = Mock()
    monkeypatch.setattr(psutil, "Process", constructor)

    for pid in (0, 123):
        assert pssafe.safe_get_process(pid) is constructor.return_value
        constructor.assert_called_with(pid)


def test_process_lookup_propagates_unexpected_exceptions(monkeypatch):
    monkeypatch.setattr(psutil, "Process", Mock(side_effect=ValueError("bug")))

    with pytest.raises(ValueError, match="bug"):
        pssafe.safe_get_process(123)
