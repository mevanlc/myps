import os
import sys

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
        self._name = os.path.basename(exe) or f"proc{pid}"

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
