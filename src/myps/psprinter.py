import os
from dataclasses import dataclass

from psutil import Process
from rich.abc import RichRenderable
from rich.style import StyleType
from rich.text import Text

from .pssafe import safe_get_cmdline, safe_get_exe, safe_get_name, safe_get_pid
from .pstree import PSTree

INCOMPLETE_ANCESTRY_MARKER = "↥"
DESCENDER_MARKER = "⤷"


@dataclass
class RichProcessStyles:
    pid_style: StyleType = "yellow"
    name_style: StyleType = "bold cyan"
    cmdline_exe_style: StyleType = "green"
    cmdline_args_style: StyleType = "dim"
    exe_style: StyleType = "bright_green"
    delimiter_style: StyleType = "dim"
    missing_parent_style: StyleType = "red"
    missing_ancestor_style: StyleType = "yellow"


class RichProcess(RichRenderable):
    def __init__(
        self, proc: Process, styles: RichProcessStyles = RichProcessStyles()
    ) -> None:
        self.proc = proc
        self.pid = safe_get_pid(proc)
        self.name = safe_get_name(proc)
        self.exe = safe_get_exe(proc)
        self.cmdline = safe_get_cmdline(proc)
        self.cmdline_exe = self.cmdline[0] if self.cmdline else ""
        self.cmdline_args = self.cmdline[1:]
        self.styles = styles
        self.proc = proc

    def append_to(self, text: Text, *, ancestry_style: StyleType | None = None) -> Text:
        text.append(self.name, style=self.styles.name_style)
        text.append(" ")
        text.append(str(self.pid), style=self.styles.pid_style)
        text.append(" ")
        if ancestry_style is not None:
            text.append(INCOMPLETE_ANCESTRY_MARKER, style=ancestry_style)
            text.append(" ")
        if self.should_show_exe():
            text.append("<", style=self.styles.delimiter_style)
            text.append(f"{self.exe}", style=self.styles.exe_style)
            text.append(">", style=self.styles.delimiter_style)
            text.append(" ")
        if self.cmdline:
            text.append(self.cmdline[0], style=self.styles.cmdline_exe_style)
            if self.cmdline_args:
                text.append(" ")
                text.append(
                    " ".join(self.cmdline_args), style=self.styles.cmdline_args_style
                )
        return text

    def __rich__(self) -> Text:
        text = Text()
        self.append_to(text)
        return text

    def is_argv0_equal_to_exe(self) -> bool:
        if not self.cmdline:
            return False
        if os.path.isabs(self.cmdline[0]) == os.path.isabs(self.exe):
            try:
                return os.path.samefile(self.cmdline[0], self.exe)
            except OSError:
                # The executable may be gone or inaccessible, or these values
                # may be process-status markers rather than filesystem paths.
                pass
        clexe_nc = os.path.normpath(os.path.normcase(self.cmdline[0]))
        exe_nc = os.path.normpath(os.path.normcase(self.exe))
        return clexe_nc == exe_nc

    def should_show_exe(self) -> bool:
        if os.path.basename(self.exe) != self.name:
            return True
        return not self.is_argv0_equal_to_exe()


class PSTreePrinter:
    def __init__(
        self,
        tree: PSTree,
        indent_pad_str: str = "  ",
        indent_descender: str = f"{DESCENDER_MARKER} ",
    ):
        self.tree = tree
        self.indent_padder = indent_pad_str
        self.indent_suffix = indent_descender
        self.last_indent_level: int | None = None

    def build_all_lines_with_map(self) -> tuple[list[Text], dict[int, Text]]:
        # Reset state for clean build
        self.last_indent_level = None

        lines: list[Text] = []
        pid_to_line: dict[int, Text] = {}

        def walk(proc: Process, depth: int) -> None:
            line = self.fmt_line(proc, depth)
            lines.append(line)
            pid_to_line[safe_get_pid(proc)] = line
            for child in self.tree.children_map.get(proc.pid, []):
                walk(child, depth + 1)

        for root in self.tree.roots:
            walk(root, 0)
        return lines, pid_to_line

    def build_lines_for_include(self, include_pids: set[int]) -> list[Text]:
        lines: list[Text] = []

        def walk(proc: Process, depth: int) -> None:
            pid = safe_get_pid(proc)
            if pid in include_pids:
                lines.append(self.fmt_line(proc, depth))
                for child in self.tree.children_map.get(proc.pid, []):
                    walk(child, depth + 1)
            else:
                # If parent not included, still might have included descendants.
                for child in self.tree.children_map.get(proc.pid, []):
                    walk(child, depth + 1)

        for root in self.tree.roots:
            walk(root, 0)
        return lines

    def fmt_line(self, proc: Process, indent_level: int) -> Text:
        rich_proc = RichProcess(proc)
        ancestry_style = None
        if rich_proc.pid in self.tree.missing_parent_pids:
            ancestry_style = rich_proc.styles.missing_parent_style
        elif rich_proc.pid in self.tree.missing_ancestor_pids:
            ancestry_style = rich_proc.styles.missing_ancestor_style

        # Determine if we should show the descender (⤷)
        # Show it only for the first item in a new indent group
        show_descender = indent_level > 0 and (
            self.last_indent_level is None or indent_level > self.last_indent_level
        )

        if indent_level > 0:
            if show_descender:
                indent_str = (self.indent_padder * indent_level) + self.indent_suffix
            else:
                # Use padding equal to descender length to maintain alignment
                indent_str = self.indent_padder * indent_level + " " * len(
                    self.indent_suffix
                )
        else:
            indent_str = ""

        # Update state
        self.last_indent_level = indent_level

        text = Text()
        text.append(indent_str)
        rich_proc.append_to(text, ancestry_style=ancestry_style)
        return text
