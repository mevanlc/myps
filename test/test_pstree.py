from unittest.mock import Mock

import psutil
import pytest
from rich.console import Console

from myps import pssafe
from myps.psprinter import PSTreePrinter
from myps.pstree import PSTree


def stub_process(pid, ppid):
    proc = Mock(spec=psutil.Process, pid=pid)
    proc.ppid.return_value = ppid
    proc.exe.return_value = f"/opt/proc{pid}"
    proc.name.return_value = f"proc{pid}"
    proc.cmdline.return_value = [proc.exe.return_value]
    return proc


@pytest.mark.parametrize("use_collected_ppids", [False, True])
def test_parent_read_once_prevents_duplicate_tree_placement(use_collected_ppids):
    parent = stub_process(100, 0)
    child = stub_process(200, parent.pid)
    child.ppid.side_effect = [parent.pid, psutil.NoSuchProcess(child.pid)]
    procs = [parent, child]
    ppids = {p.pid: p.ppid() for p in procs} if use_collected_ppids else None

    tree = PSTree.from_processes(procs, ppid_by_pid=ppids)

    for proc in procs:
        proc.ppid.assert_called_once_with()
    assert tree.roots == [parent]
    assert tree.children_map[parent.pid] == [child]
    assert tree.parent_map == {child.pid: parent.pid}
    lines, _ = PSTreePrinter(tree).build_all_lines_with_map()
    assert len(lines) == 2
    assert sum("proc200 200" in line.plain for line in lines) == 1


@pytest.mark.parametrize(
    "sentinel", [pssafe.NSP_NUMERIC, pssafe.ZP_NUMERIC, pssafe.AD_NUMERIC]
)
def test_unreadable_parent_marks_descendants_before_filtering(sentinel):
    root = stub_process(100, sentinel)
    child = stub_process(200, root.pid)
    grandchild = stub_process(300, child.pid)
    normal_root = stub_process(400, 0)
    # An absent parent alone is not evidence that a lookup failed.
    outside_scope = stub_process(500, 999)
    tree = PSTree.from_processes([root, child, grandchild, normal_root, outside_scope])

    assert tree.missing_parent_pids == {root.pid}
    assert tree.missing_ancestor_pids == {child.pid, grandchild.pid}
    printer = PSTreePrinter(tree)
    _, pid_to_line = printer.build_all_lines_with_map()
    console = Console()
    for pid, color in ((100, "red"), (200, "yellow"), (300, "yellow")):
        line = pid_to_line[pid]
        assert f"{pid} ↥ " in line.plain
        marker_index = line.plain.index("↥")
        assert line.get_style_at_offset(console, marker_index).color.name == color
    assert "↥" not in pid_to_line[400].plain
    assert "↥" not in pid_to_line[500].plain

    lines = printer.build_lines_for_include({grandchild.pid})
    assert len(lines) == 1
    assert "300 ↥ " in lines[0].plain
    marker_index = lines[0].plain.index("↥")
    assert lines[0].get_style_at_offset(console, marker_index).color.name == "yellow"


def test_immediate_failure_takes_precedence_over_ancestor_failure():
    root = stub_process(100, 0)
    child = stub_process(200, root.pid)
    grandchild = stub_process(300, child.pid)
    tree = PSTree.from_processes(
        [root, child, grandchild], missing_parent_pids={root.pid, child.pid}
    )

    assert tree.missing_parent_pids == {root.pid, child.pid}
    assert tree.missing_ancestor_pids == {grandchild.pid}
    _, lines = PSTreePrinter(tree).build_all_lines_with_map()
    marker_index = lines[child.pid].plain.index("↥")
    assert (
        lines[child.pid].get_style_at_offset(Console(), marker_index).color.name
        == "red"
    )
