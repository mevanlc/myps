#!/usr/bin/env python3

# pyright: strict

import argparse
import fnmatch
import os
import re
import shutil
import signal
import sys
import textwrap
from functools import wraps
from typing import Literal

import psutil
from psutil import Process
from rich.console import Console

import myps.pssafe
from myps import configutil, pstree
from myps.psprinter import PSTreePrinter
from myps.pstree import PSTree

console: Console | None = None

TRUNC_INDICATOR = "…"


def main() -> None:
    # Redirected stdout commonly defaults to a legacy code page on Windows,
    # which cannot encode the tree and process-status glyphs used by myps.
    if os.name == "nt" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    # Reset SIGPIPE to default behavior to avoid BrokenPipeError when piping to less/head/etc
    sigpipe = getattr(signal, "SIGPIPE", None)
    if sigpipe is not None:
        signal.signal(sigpipe, signal.SIG_DFL)
    raise SystemExit(cli_main())


UserIdentity = tuple[Literal["uid"], int] | tuple[Literal["username"], str]


def current_user_identity() -> UserIdentity:
    """Return an identity suitable for comparing process ownership on this OS."""
    getuid = getattr(os, "getuid", None)
    if getuid is not None:
        return ("uid", int(getuid()))
    return ("username", psutil.Process().username().casefold())


def process_belongs_to_user(proc: Process, identity: UserIdentity) -> bool:
    kind, value = identity
    if kind == "uid":
        return value in proc.uids()
    return proc.username().casefold() == value


def myps_invocation_pids(processes: list[Process], thispid: int) -> set[int]:
    """Return the launcher, runtime process, and descendants for this invocation."""
    pid_map = {myps.pssafe.safe_get_pid(proc): proc for proc in processes}
    current = pid_map.get(thispid)
    invocation_root_pid = thispid

    # Windows entry-point launchers such as myps.exe can have one or more Python
    # runtime processes below them. Treat the launcher as self so the entire
    # invocation is hidden.
    while current is not None:
        parent_pid = myps.pssafe.safe_get_ppid(current)
        parent = pid_map.get(parent_pid)
        if parent is None:
            break
        if is_myps_launcher(parent):
            invocation_root_pid = parent_pid
            break
        if not is_python_runtime(parent):
            break
        current = parent

    children_by_parent: dict[int, list[int]] = {}
    for proc in processes:
        pid = myps.pssafe.safe_get_pid(proc)
        ppid = myps.pssafe.safe_get_ppid(proc)
        children_by_parent.setdefault(ppid, []).append(pid)

    invocation_pids: set[int] = set()
    pending = [invocation_root_pid]
    while pending:
        pid = pending.pop()
        if pid in invocation_pids:
            continue
        invocation_pids.add(pid)
        pending.extend(children_by_parent.get(pid, []))
    return invocation_pids


def is_myps_launcher(proc: Process) -> bool:
    return process_name_stem(proc) == "myps"


def is_python_runtime(proc: Process) -> bool:
    return process_name_stem(proc) in {"py", "python", "pythonw"}


def process_name_stem(proc: Process) -> str:
    name = myps.pssafe.safe_get_name(proc)
    exe_name = os.path.basename(myps.pssafe.safe_get_exe(proc))
    for value in (name, exe_name):
        stem = os.path.splitext(value)[0].casefold()
        if stem:
            return stem
    return ""


class MyHelpFormatter(
    argparse.RawDescriptionHelpFormatter, argparse.ArgumentDefaultsHelpFormatter
):
    pass


def cli_main() -> int:
    parser = argparse.ArgumentParser(
        epilog=textwrap.dedent(
            f"""
            --init-config writes an example config to the default path
            ({configutil.DEFAULT_CONFIG_PATH}) or to the file given by -c/--config.

            Filters match against each process's full command line string.
            
            """
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-b",
        "--bare",
        action="store_true",
        help="Print bare list of PIDs instead of the process tree",
    )
    parser.add_argument(
        "-f", "--full", action="store_true", help="Disable terminal-width truncation"
    )
    parser.add_argument(
        "-r",
        "--regex",
        action="store_true",
        help="Interpret pattern as a regular expression",
    )
    parser.add_argument(
        "-C",
        "--case",
        action="store_true",
        help="Case-sensitive pattern matching (default is case-insensitive)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose output for debugging",
    )
    parser.add_argument(
        "-k",
        "--keep-ancestors",
        action="store_true",
        help="Keep ancestors of processes matching pattern",
    )
    parser.add_argument(
        "-K",
        "--keep-children",
        action="store_true",
        help="Keep descendants of processes matching pattern",
    )
    parser.add_argument(
        "--include-self",
        action="store_true",
        help="Include the running myps invocation and its descendants",
    )
    parser.add_argument(
        "--color",
        choices=["always", "auto", "never"],
        default="auto",
        help="Control colored output (default: auto)",
    )
    config_group = parser.add_mutually_exclusive_group()
    config_group.add_argument(
        "-c",
        "--config",
        dest="config_path",
        metavar="FILE",
        help=f"Read config from <file> instead of {configutil.DEFAULT_CONFIG_PATH}",
    )
    config_group.add_argument(
        "--no-config",
        action="store_true",
        help="Do not read a config file",
    )
    parser.add_argument(
        "--init-config",
        action="store_true",
        help="Write example config to the target path and exit",
    )
    parser.add_argument(
        "filter_pattern",
        nargs="?",
        default="",
        help="Pattern to filter processes by",
        metavar="PATTERN",
    )
    args = parser.parse_args(namespace=CliArgs())
    if args.no_config and args.init_config:
        parser.error("--no-config cannot be used with --init-config")
    if args.verbose:
        print(f"Args: {args}")

    config_path = configutil.resolve_config_path(args.config_path)

    if args.init_config:
        try:
            written_path = configutil.write_sample_config(config_path)
        except FileExistsError as exc:
            print(exc, file=sys.stderr)
            return 1
        except OSError as exc:
            print(f"Failed to write config file {config_path}: {exc}", file=sys.stderr)
            return 1
        print(f"Wrote example config to {written_path}")
        return 0

    if args.no_config:
        config = configutil.MypsConfig(skip_patterns=[], keep_patterns=[])
    else:
        try:
            config = configutil.load_config(config_path)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 1

    if args.full or not sys.stdout.isatty():
        term_width = 0
    else:
        term_width = shutil.get_terminal_size((80, 20)).columns

    user_identity = current_user_identity()
    thispid = os.getpid()

    skip_pattern = config.skip_re
    keep_pattern = config.keep_re

    all_procs = list(psutil.process_iter())
    excluded_pids = (
        set() if args.include_self else myps_invocation_pids(all_procs, thispid)
    )

    myprocs: list[Process] = []
    for proc in all_procs:
        if myps.pssafe.safe_get_pid(proc) in excluded_pids:
            continue
        try:
            if process_belongs_to_user(proc, user_identity):
                exe = proc.exe()
                skip_match = skip_pattern.search(exe) if skip_pattern else None
                keep_match = keep_pattern.search(exe) if keep_pattern else None
                should_skip = bool(skip_match and not keep_match)
                if should_skip:
                    continue
                myprocs.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass

    ppid_by_pid: dict[int, int] = {}
    missing_parents: dict[int, list[int]] = {}
    missing_parent_pids: set[int] = set()
    myproc_pids = {myps.pssafe.safe_get_pid(p) for p in myprocs}
    for proc in myprocs:
        pid = myps.pssafe.safe_get_pid(proc)
        ppid = myps.pssafe.safe_get_ppid(proc)
        ppid_by_pid[pid] = ppid
        if ppid > 0 and ppid not in myproc_pids and ppid not in excluded_pids:
            missing_parents.setdefault(ppid, []).append(pid)
    for missing_parent_pid, child_pids in missing_parents.items():
        parent_proc = myps.pssafe.safe_get_process(missing_parent_pid)
        if parent_proc is None:
            missing_parent_pids.update(child_pids)
        else:
            myprocs.append(parent_proc)
            ppid_by_pid[missing_parent_pid] = myps.pssafe.safe_get_ppid(parent_proc)

    re_pattern: re.Pattern[str] | None = None
    if args.filter_pattern:
        flags = 0 if args.case else re.IGNORECASE
        if args.regex:
            re_pattern = re.compile(args.filter_pattern, flags)
        else:
            pattern = fnmatch.translate(args.filter_pattern)
            if pattern[-2:].lower() == r"\z":
                pattern = pattern[:-2]
            re_pattern = re.compile(pattern, flags)

    if args.verbose:
        if args.filter_pattern:
            print(
                f"Pattern: {args.filter_pattern!r}, Regex: {args.regex}, Case: {args.case}"
            )
            print(f"Compiled regex: {re_pattern.pattern if re_pattern else 'None'}")
        print(f"Total processes for user {user_identity[1]}: {len(myprocs)}")

    if myprocs:
        tree = PSTree.from_processes(
            myprocs,
            ppid_by_pid=ppid_by_pid,
            missing_parent_pids=missing_parent_pids,
        )
        printer = PSTreePrinter(tree)

        # Determine if we should use colored output
        if args.color == "always":
            use_rich_output = True
            force_terminal = True
        elif args.color == "never":
            use_rich_output = False
            force_terminal = False
        else:  # auto
            use_rich_output = sys.stdout.isatty()
            force_terminal = None

        console = Console(force_terminal=force_terminal)

        # Build full pre-order traversal once so we can both search and print
        full_lines, pid_to_line = printer.build_all_lines_with_map()

        if re_pattern:
            # Identify matching PIDs by searching their full formatted line
            matching_pids = [
                pid
                for pid, line in pid_to_line.items()
                if re_pattern.search(line.plain)
            ]
            if args.keep_ancestors or args.keep_children:
                matching_set = set(matching_pids)
                include_pids = set(matching_set)
                if args.keep_ancestors:
                    include_pids.update(tree.include_with_ancestors(matching_set))
                if args.keep_children:
                    include_pids.update(tree.include_with_descendants(matching_set))
                selected_pids = [pid for pid in pid_to_line if pid in include_pids]
                lines = printer.build_lines_for_include(include_pids)
            else:
                selected_pids = matching_pids
                lines = [pid_to_line[pid] for pid in selected_pids]
        else:
            selected_pids = list(pid_to_line.keys())
            lines = full_lines

        if args.bare:
            for pid in selected_pids:
                print(pid)
        else:
            for ln in lines:
                if term_width:
                    ln.truncate(term_width, overflow="ellipsis")
                if use_rich_output:
                    console.print(ln, highlight=False)
                else:
                    print(ln.plain)
        if args.verbose and re_pattern:
            print(f"Matched lines: {len(lines)}")
    elif not args.bare:
        print("No matching processes found for current user.")

    return 0


def build_proc_graph(
    processes: list[Process],
) -> tuple[list[Process], dict[int, list[Process]]]:
    # Backward-compat shim for prior function; now delegates to PSTree
    tree = pstree.PSTree.from_processes(processes)
    return tree.roots, tree.children_map


def build_tree(
    roots: list[Process], children_map: dict[int, list[Process]]
) -> list[str]:
    # Backward-compat helper, now implemented via PSTreePrinter
    tree = pstree.PSTree(roots=roots, children_map=children_map)
    printer = PSTreePrinter(tree)
    lines, _ = printer.build_all_lines_with_map()
    return [line.plain for line in lines]


VALID_PYTHON_PRINT_KWARGS = {"sep", "end", "file", "flush"}


# FYI rich.Console.print has this signature:
# @overload
# def print(
#     *values: object, sep: str | None = " ", end: str | None = "\n", file: SupportsWrite[str] | None = None, flush: Literal[False] = False,
# ) -> None: ...
# @overload
# def print(
#     *values: object, sep: str | None = " ", end: str | None = "\n", file: _SupportsWriteAndFlush[str] | None = None, flush: bool
# ) -> None: ...
@wraps(Console.print, assigned=("__signature__",))
def console_print(*args: object, **kwargs: object) -> None:
    global console
    if console is None:
        fwd_kwargs = {k: v for k, v in kwargs.items() if k in VALID_PYTHON_PRINT_KWARGS}
        print(*args, **fwd_kwargs)  # type: ignore
    else:
        console.print(*args, **kwargs)  # type: ignore


class CliArgs:
    bare: bool = False
    full: bool = False
    regex: bool = False
    filter_pattern: str = ""
    case: bool = False
    verbose: bool = False
    keep_ancestors: bool = False
    keep_children: bool = False
    include_self: bool = False
    color: str = "auto"
    config_path: str | None = None
    no_config: bool = False
    init_config: bool = False


if __name__ == "__main__":
    main()
