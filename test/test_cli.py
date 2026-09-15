import os
import sys
from unittest.mock import Mock

import psutil
import pytest

from myps import cli, configutil, psprinter, pssafe


class StubProcess:
    def __init__(
        self,
        pid: int,
        exe: str,
        uids: tuple[int, int, int],
        ppid: int,
        username: str = "example\\user",
    ):
        self.pid = pid
        self._exe = exe
        self._uids = uids
        self._ppid = ppid
        self._username = username
        self._cmdline = [exe]
        self._name = os.path.basename(exe.replace("\\", "/")) or f"proc{pid}"

    def uids(self):
        return self._uids

    def exe(self):
        return self._exe

    def ppid(self):
        return self._ppid

    def cmdline(self):
        return list(self._cmdline)

    def name(self):
        return self._name

    def username(self):
        return self._username


@pytest.fixture(autouse=True)
def restore_cli_args():
    original = list(sys.argv)
    try:
        yield
    finally:
        sys.argv = original


def test_cli_init_config(tmp_path, monkeypatch, capsys):
    target = tmp_path / "init.toml"
    sys.argv = ["myps", "--init-config", "-c", str(target)]
    rc = cli.cli_main()
    assert rc == 0
    assert target.exists()

    sys.argv = ["myps", "--init-config", "-c", str(target)]
    rc = cli.cli_main()
    assert rc == 1
    err = capsys.readouterr().err
    assert "already exists" in err


def test_cli_filters_respect_config(tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "cfg.toml"
    config_path.write_text(
        """
[regexSkipPatterns]
system = '^/System/'

[regexKeepPatterns]
terminal = 'Terminal.app/'
        """.strip()
    )

    user_uid = 501
    terminal_proc = StubProcess(
        pid=200,
        exe="/Applications/Utilities/Terminal.app/MacOS/Terminal",
        uids=(user_uid, user_uid, user_uid),
        ppid=1,
    )
    system_proc = StubProcess(
        pid=300,
        exe="/System/Library/CoreServices/Finder.app/Contents/MacOS/Finder",
        uids=(user_uid, user_uid, user_uid),
        ppid=1,
    )
    parent_proc = StubProcess(
        pid=1,
        exe="/sbin/launchd",
        uids=(user_uid, user_uid, user_uid),
        ppid=0,
    )

    procs = [terminal_proc, system_proc]
    proc_map = {p.pid: p for p in procs + [parent_proc]}

    monkeypatch.setattr(cli, "current_user_identity", lambda: ("uid", user_uid))
    monkeypatch.setattr(cli.psutil, "process_iter", lambda: iter(procs))
    monkeypatch.setattr(pssafe, "safe_get_process", lambda pid: proc_map.get(pid))
    monkeypatch.setattr(
        psprinter.RichProcess, "is_argv0_equal_to_exe", lambda self: True
    )

    sys.argv = [
        "myps",
        "--full",
        "--color",
        "never",
        "-c",
        str(config_path),
    ]

    rc = cli.cli_main()
    assert rc == 0
    out = capsys.readouterr().out
    assert "Terminal" in out
    assert "Finder" not in out


@pytest.mark.parametrize("keep_children_flag", ["-K", "--keep-children"])
def test_cli_keep_children_includes_full_matching_subtree(
    keep_children_flag, monkeypatch, capsys
):
    user_uid = 501
    ancestor = StubProcess(100, "/opt/ancestor", (user_uid,) * 3, 1)
    matched = StubProcess(200, "/opt/matched", (user_uid,) * 3, ancestor.pid)
    child = StubProcess(300, "/opt/child", (user_uid,) * 3, matched.pid)
    grandchild = StubProcess(400, "/opt/grandchild", (user_uid,) * 3, child.pid)
    sibling = StubProcess(500, "/opt/sibling", (user_uid,) * 3, ancestor.pid)
    procs = [ancestor, matched, child, grandchild, sibling]

    monkeypatch.setattr(cli, "current_user_identity", lambda: ("uid", user_uid))
    monkeypatch.setattr(cli.psutil, "process_iter", lambda: iter(procs))
    monkeypatch.setattr(pssafe, "safe_get_process", lambda _pid: None)
    monkeypatch.setattr(
        psprinter.RichProcess, "is_argv0_equal_to_exe", lambda self: True
    )

    sys.argv = [
        "myps",
        "--full",
        "--color",
        "never",
        "--no-config",
        keep_children_flag,
        "matched",
    ]
    assert cli.cli_main() == 0
    out = capsys.readouterr().out
    assert "matched 200" in out
    assert "child 300" in out
    assert "grandchild 400" in out
    assert "ancestor 100" not in out
    assert "sibling 500" not in out


def test_cli_keep_ancestors_and_children_expand_only_from_direct_matches(
    monkeypatch, capsys
):
    user_uid = 501
    root = StubProcess(100, "/opt/root", (user_uid,) * 3, 1)
    ancestor = StubProcess(200, "/opt/ancestor", (user_uid,) * 3, root.pid)
    matched = StubProcess(300, "/opt/matched", (user_uid,) * 3, ancestor.pid)
    child = StubProcess(400, "/opt/child", (user_uid,) * 3, matched.pid)
    ancestor_child = StubProcess(
        500, "/opt/ancestor-child", (user_uid,) * 3, ancestor.pid
    )
    root_child = StubProcess(600, "/opt/root-child", (user_uid,) * 3, root.pid)
    procs = [root, ancestor, matched, child, ancestor_child, root_child]

    monkeypatch.setattr(cli, "current_user_identity", lambda: ("uid", user_uid))
    monkeypatch.setattr(cli.psutil, "process_iter", lambda: iter(procs))
    monkeypatch.setattr(pssafe, "safe_get_process", lambda _pid: None)
    monkeypatch.setattr(
        psprinter.RichProcess, "is_argv0_equal_to_exe", lambda self: True
    )

    sys.argv = [
        "myps",
        "--full",
        "--color",
        "never",
        "--no-config",
        "-k",
        "-K",
        "matched",
    ]
    assert cli.cli_main() == 0
    out = capsys.readouterr().out
    assert "root 100" in out
    assert "ancestor 200" in out
    assert "matched 300" in out
    assert "child 400" in out
    assert "ancestor-child 500" not in out
    assert "root-child 600" not in out


def test_cli_include_self(monkeypatch, capsys):
    user_uid = 501
    self_proc = StubProcess(
        pid=os.getpid(),
        exe="/usr/local/bin/myps",
        uids=(user_uid, user_uid, user_uid),
        ppid=1,
    )

    monkeypatch.setattr(cli, "current_user_identity", lambda: ("uid", user_uid))
    monkeypatch.setattr(cli.psutil, "process_iter", lambda: iter([self_proc]))
    monkeypatch.setattr(pssafe, "safe_get_process", lambda _pid: None)
    monkeypatch.setattr(
        psprinter.RichProcess, "is_argv0_equal_to_exe", lambda self: True
    )

    base_args = [
        "myps",
        "--full",
        "--color",
        "never",
        "--no-config",
        "-k",
        "myps",
    ]
    sys.argv = base_args
    assert cli.cli_main() == 0
    assert capsys.readouterr().out == "No matching processes found for current user.\n"

    sys.argv = [*base_args, "--include-self"]
    assert cli.cli_main() == 0
    assert f"myps {os.getpid()}" in capsys.readouterr().out


def test_cli_excludes_self_launcher_and_descendants(monkeypatch, capsys):
    user_uid = 501
    launcher_proc = StubProcess(
        pid=100,
        exe=r"C:\Users\example\.local\bin\myps.exe",
        uids=(user_uid, user_uid, user_uid),
        ppid=1,
    )
    self_proc = StubProcess(
        pid=os.getpid(),
        exe=r"C:\Users\example\AppData\Roaming\uv\tools\myps\python.exe",
        uids=(user_uid, user_uid, user_uid),
        ppid=200,
    )
    runtime_proc = StubProcess(
        pid=200,
        exe=r"C:\Users\example\AppData\Roaming\uv\tools\myps\python.exe",
        uids=(user_uid, user_uid, user_uid),
        ppid=launcher_proc.pid,
    )
    child_proc = StubProcess(
        pid=300,
        exe=r"C:\Program Files\example\copilot.exe",
        uids=(user_uid, user_uid, user_uid),
        ppid=self_proc.pid,
    )
    launcher_proc._cmdline.append("copilot")
    self_proc._cmdline.extend([launcher_proc.exe(), "copilot"])

    procs = [launcher_proc, runtime_proc, self_proc, child_proc]
    monkeypatch.setattr(cli, "current_user_identity", lambda: ("uid", user_uid))
    monkeypatch.setattr(cli.psutil, "process_iter", lambda: iter(procs))
    monkeypatch.setattr(pssafe, "safe_get_process", lambda _pid: None)
    monkeypatch.setattr(
        psprinter.RichProcess, "is_argv0_equal_to_exe", lambda self: True
    )

    base_args = ["myps", "--full", "--color", "never", "--no-config"]
    sys.argv = base_args
    assert cli.cli_main() == 0
    assert capsys.readouterr().out == "No matching processes found for current user.\n"

    sys.argv = [*base_args, "--include-self"]
    assert cli.cli_main() == 0
    out = capsys.readouterr().out
    assert "myps.exe 100" in out
    assert "python.exe 200" in out
    assert f"python.exe {os.getpid()}" in out
    assert "copilot.exe 300" in out


def test_cli_no_config_ignores_default_config(tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "config.toml"
    config_path.write_text("[regexSkipPatterns]\neverything = '.*'\n")
    monkeypatch.setattr(configutil, "DEFAULT_CONFIG_PATH", config_path)

    user_uid = 501
    proc = StubProcess(
        pid=200,
        exe="/usr/local/bin/example",
        uids=(user_uid, user_uid, user_uid),
        ppid=1,
    )
    monkeypatch.setattr(cli, "current_user_identity", lambda: ("uid", user_uid))
    monkeypatch.setattr(cli.psutil, "process_iter", lambda: iter([proc]))
    monkeypatch.setattr(pssafe, "safe_get_process", lambda _pid: None)
    monkeypatch.setattr(
        psprinter.RichProcess, "is_argv0_equal_to_exe", lambda self: True
    )

    sys.argv = ["myps", "--full", "--color", "never"]
    assert cli.cli_main() == 0
    assert capsys.readouterr().out == "No matching processes found for current user.\n"

    sys.argv = ["myps", "--full", "--color", "never", "--no-config"]
    assert cli.cli_main() == 0
    assert "example 200" in capsys.readouterr().out


@pytest.mark.parametrize(
    "args",
    [
        ["--no-config", "--config", "config.toml"],
        ["--no-config", "--init-config"],
    ],
)
def test_cli_rejects_conflicting_config_options(args):
    sys.argv = ["myps", *args]
    with pytest.raises(SystemExit) as exc_info:
        cli.cli_main()
    assert exc_info.value.code == 2


def test_main_propagates_unexpected_exceptions(monkeypatch):
    class Boom(Exception):
        pass

    monkeypatch.setattr(cli, "cli_main", lambda: (_ for _ in ()).throw(Boom("boom")))

    with pytest.raises(Boom, match="boom"):
        cli.main()


def test_username_identity_is_case_insensitive():
    proc = StubProcess(
        pid=123,
        exe=r"C:\Program Files\Example\example.exe",
        uids=(501, 501, 501),
        ppid=1,
        username="EXAMPLE\\Alice",
    )

    assert cli.process_belongs_to_user(proc, ("username", "example\\alice"))


def test_cli_username_identity_excludes_other_users(monkeypatch, capsys):
    own_proc = StubProcess(
        pid=123,
        exe=r"C:\Users\Alice\example.exe",
        uids=(501, 501, 501),
        ppid=1,
        username="EXAMPLE\\Alice",
    )
    other_proc = StubProcess(
        pid=456,
        exe=r"C:\Users\Bob\other.exe",
        uids=(502, 502, 502),
        ppid=1,
        username="EXAMPLE\\Bob",
    )
    monkeypatch.setattr(
        cli, "current_user_identity", lambda: ("username", "example\\alice")
    )
    monkeypatch.setattr(
        cli.psutil, "process_iter", lambda: iter([own_proc, other_proc])
    )
    monkeypatch.setattr(pssafe, "safe_get_process", lambda _pid: None)
    monkeypatch.setattr(
        psprinter.RichProcess, "is_argv0_equal_to_exe", lambda self: True
    )
    sys.argv = ["myps", "--full", "--color", "never", "--no-config"]

    assert cli.cli_main() == 0
    out = capsys.readouterr().out
    assert "example.exe 123" in out
    assert "other.exe 456" not in out


def test_rich_process_mismatch_renders_exe_instead_of_raising():
    proc = StubProcess(
        pid=123,
        exe="/opt/homebrew/libexec/git-core/git-remote-http",
        uids=(501, 501, 501),
        ppid=1,
    )
    proc._name = "git-remote-https"
    proc._cmdline = ["/opt/homebrew/opt/git/libexec/git-core/git-remote-https"]

    rendered = psprinter.RichProcess(proc).__rich__().plain

    assert "git-remote-https 123" in rendered
    assert "</opt/homebrew/libexec/git-core/git-remote-http>" in rendered
    assert "/opt/homebrew/opt/git/libexec/git-core/git-remote-https" in rendered


@pytest.mark.parametrize(
    ("error_type", "marker"),
    [
        (psutil.NoSuchProcess, pssafe.NSP_STRING),
        (psutil.ZombieProcess, pssafe.ZP_STRING),
        (psutil.AccessDenied, pssafe.AD_STRING),
    ],
)
def test_cli_process_disappears_during_parent_collection(
    error_type, marker, monkeypatch, capsys
):
    parent = StubProcess(100, "/opt/cargo", (501,) * 3, 1)
    rust = StubProcess(200, "/opt/rustc", (501,) * 3, parent.pid)
    linker = StubProcess(300, "/opt/linker", (501,) * 3, rust.pid)
    procs = [parent, rust, linker]

    def disappear():
        for method in ("exe", "name", "cmdline"):
            monkeypatch.setattr(
                parent, method, Mock(side_effect=error_type(parent.pid))
            )
        raise error_type(parent.pid)

    monkeypatch.setattr(parent, "ppid", disappear)
    for proc in (rust, linker):
        monkeypatch.setattr(proc, "ppid", Mock(return_value=proc._ppid))
    constructor = Mock(side_effect=AssertionError("unexpected parent lookup"))
    monkeypatch.setattr(psutil, "Process", constructor)
    monkeypatch.setattr(cli, "current_user_identity", lambda: ("uid", 501))
    monkeypatch.setattr(psutil, "process_iter", lambda: iter(procs))
    sys.argv = [
        "myps",
        "--no-config",
        "--color",
        "never",
        "--include-self",
        "-Kk",
        "rust",
    ]

    assert cli.cli_main() == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert f"{marker} 100 ↥? " in output.out
    assert "rustc 200 ↥? " in output.out
    assert "linker 300 ↥? " in output.out
    assert len(output.out.splitlines()) == 3
    constructor.assert_not_called()
    for proc in (rust, linker):
        proc.ppid.assert_called_once_with()


@pytest.mark.parametrize("color", ["never", "always"])
def test_cli_missing_parent_marks_all_children(color, monkeypatch, capsys):
    monkeypatch.delenv("NO_COLOR", raising=False)
    children = [
        StubProcess(200, "/opt/rustc", (501,) * 3, 100),
        StubProcess(300, "/opt/rustfmt", (501,) * 3, 100),
    ]
    constructor = Mock(side_effect=psutil.NoSuchProcess(100))
    monkeypatch.setattr(psutil, "Process", constructor)
    monkeypatch.setattr(cli, "current_user_identity", lambda: ("uid", 501))
    monkeypatch.setattr(psutil, "process_iter", lambda: iter(children))
    sys.argv = ["myps", "--no-config", "--color", color, "-Kk", "rust"]

    assert cli.cli_main() == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert output.out.count("↥?") == 2
    if color == "always":
        assert output.out.count("\x1b[31m↥?") == 2
    else:
        assert "rustc 200 ↥? " in output.out
        assert "rustfmt 300 ↥? " in output.out
        assert "\x1b" not in output.out
    constructor.assert_called_once_with(100)


@pytest.mark.parametrize("fetched_parent_disappears", [False, True])
def test_cli_reuses_collected_ppids_for_fetched_parents(
    fetched_parent_disappears, monkeypatch, capsys
):
    parent = StubProcess(100, "/opt/cargo", (501,) * 3, 0)
    child = StubProcess(200, "/opt/rustc", (501,) * 3, parent.pid)
    parent_ppid = psutil.NoSuchProcess(parent.pid) if fetched_parent_disappears else 0
    monkeypatch.setattr(parent, "ppid", Mock(side_effect=[parent_ppid]))
    # Invocation detection and parent collection each read once; construction
    # and both rendering passes must use the collected parent link.
    monkeypatch.setattr(child, "ppid", Mock(side_effect=[parent.pid, parent.pid]))
    constructor = Mock(return_value=parent)
    monkeypatch.setattr(psutil, "Process", constructor)
    monkeypatch.setattr(cli, "current_user_identity", lambda: ("uid", 501))
    monkeypatch.setattr(psutil, "process_iter", lambda: iter([child]))
    sys.argv = ["myps", "--no-config", "--color", "never", "-Kk", "rust"]

    assert cli.cli_main() == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("cargo 100 ")
    assert lines[1].startswith("  ⤷ rustc 200 ")
    assert ("↥?" in lines[0]) is fetched_parent_disappears
    assert ("↥?" in lines[1]) is fetched_parent_disappears
    parent.ppid.assert_called_once_with()
    assert child.ppid.call_count == 2
    constructor.assert_called_once_with(parent.pid)


@pytest.mark.parametrize("parent_kind", ["root", "excluded", "outside_scope"])
def test_cli_intentional_ancestry_boundaries_are_unmarked(
    parent_kind, monkeypatch, capsys
):
    root = StubProcess(100, "/opt/root", (501,) * 3, 0)
    proc = StubProcess(200, "/opt/rustc", (501,) * 3, root.pid)
    parent_lookup = Mock(side_effect=AssertionError("unexpected parent lookup"))
    procs = [proc]
    if parent_kind == "root":
        proc._ppid = 0
    elif parent_kind == "excluded":
        root.pid = os.getpid()
        proc._ppid = root.pid
        # Skip this process from invocation detection so it can exercise the
        # deliberate exclusion of an otherwise missing parent during collection.
        procs = [root, proc]
        monkeypatch.setattr(cli, "myps_invocation_pids", lambda *_args: {root.pid})
    else:
        root._ppid = 999
        parent_lookup = Mock(return_value=root)

    monkeypatch.setattr(pssafe, "safe_get_process", parent_lookup)
    monkeypatch.setattr(cli, "current_user_identity", lambda: ("uid", 501))
    monkeypatch.setattr(psutil, "process_iter", lambda: iter(procs))
    sys.argv = ["myps", "--no-config", "--color", "never", "-Kk", "rust"]

    assert cli.cli_main() == 0
    out = capsys.readouterr().out
    assert "rustc 200 " in out
    assert "↥?" not in out
    if parent_kind == "outside_scope":
        assert "root 100 " in out
        parent_lookup.assert_called_once_with(root.pid)
    else:
        parent_lookup.assert_not_called()


@pytest.mark.parametrize("error_type", [FileNotFoundError, PermissionError])
@pytest.mark.parametrize("same_path", [False, True])
def test_rich_process_unavailable_executable_falls_back_to_path_comparison(
    error_type, same_path, monkeypatch
):
    proc = StubProcess(123, "/opt/tool", (501,) * 3, 1)
    proc._cmdline = ["/opt/./tool" if same_path else "/other/tool"]
    monkeypatch.setattr(os.path, "samefile", Mock(side_effect=error_type()))

    rich_proc = psprinter.RichProcess(proc)
    assert rich_proc.is_argv0_equal_to_exe() is same_path
    rendered = rich_proc.__rich__().plain
    assert ("</opt/tool>" in rendered) is not same_path
    assert proc._cmdline[0] in rendered
