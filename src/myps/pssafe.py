import psutil
from psutil import Process

AD_NUMERIC = -9999
AD_STRING = "⛔️"

ZP_NUMERIC = -8888
ZP_STRING = "🧟"

NSP_NUMERIC = -7777
NSP_STRING = "🪦"


def safe_get_cmdline(proc: Process) -> list[str]:
    try:
        return proc.cmdline()
    except psutil.ZombieProcess:
        return [ZP_STRING]
    except psutil.NoSuchProcess:
        return [NSP_STRING]
    except psutil.AccessDenied:
        return [AD_STRING]


def safe_get_process(pid: int) -> Process | None:
    if pid < 0:
        return None
    try:
        return psutil.Process(pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def safe_get_ppid(proc: Process) -> int:
    try:
        return proc.ppid()
    except psutil.ZombieProcess:
        return ZP_NUMERIC
    except psutil.NoSuchProcess:
        return NSP_NUMERIC
    except psutil.AccessDenied:
        return AD_NUMERIC


def safe_get_exe(proc: Process) -> str:
    try:
        return proc.exe()
    except psutil.ZombieProcess:
        return ZP_STRING
    except psutil.NoSuchProcess:
        return NSP_STRING
    except psutil.AccessDenied:
        return AD_STRING


def safe_get_name(proc: Process) -> str:
    try:
        return proc.name()
    except psutil.ZombieProcess:
        return ZP_STRING
    except psutil.NoSuchProcess:
        return NSP_STRING
    except psutil.AccessDenied:
        return AD_STRING


def safe_get_pid(proc: Process) -> int:
    if proc:
        return proc.pid
    return NSP_NUMERIC
