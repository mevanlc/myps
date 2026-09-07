from psutil import Process

from myps.pssafe import safe_get_exe, safe_get_pid, safe_get_ppid


class PSTree:
    def __init__(self, *, roots: list[Process], children_map: dict[int, list[Process]]):
        self.roots = roots
        self.children_map = children_map
        # Build parent map for quick ancestor lookups
        parent_map: dict[int, int] = {}
        for parent, kids in children_map.items():
            for child in kids:
                parent_map[safe_get_pid(child)] = parent
        self.parent_map = parent_map

    @classmethod
    def from_processes(cls, processes: list[Process]) -> "PSTree":
        # Map pid -> process and parent -> [children]
        pid_map: dict[int, Process] = {}
        for p in processes:
            pid = safe_get_pid(p)
            if pid:
                pid_map[pid] = p

        children_map: dict[int, list[Process]] = {pid: [] for pid in pid_map.keys()}

        for pid, p in list(pid_map.items()):
            ppid = safe_get_ppid(p)
            if ppid in pid_map:
                children_map[ppid].append(p)

        # Roots are those whose parent is not in the kept set
        roots: list[Process] = []
        for pid, p in pid_map.items():
            ppid = safe_get_ppid(p)
            if ppid not in pid_map:
                roots.append(p)

        # Sort children lists for stable output
        for kids in children_map.values():
            kids.sort(key=proc_key)

        roots.sort(key=proc_key)
        return cls(roots=roots, children_map=children_map)

    def include_with_ancestors(self, base_pids: set[int]) -> set[int]:
        """Return a set with base pids plus all their ancestors up to roots."""
        include: set[int] = set()
        for pid in base_pids:
            cur = pid
            while cur and cur not in include:
                include.add(cur)
                cur = self.parent_map.get(cur, 0)
        return include

    def include_with_descendants(self, base_pids: set[int]) -> set[int]:
        """Return a set with base pids plus all their descendants."""
        include: set[int] = set()
        pending = list(base_pids)
        while pending:
            pid = pending.pop()
            if pid in include:
                continue
            include.add(pid)
            pending.extend(
                safe_get_pid(child) for child in self.children_map.get(pid, [])
            )
        return include


def proc_key(proc: Process) -> tuple[str, int]:
    exe = safe_get_exe(proc)
    pid = safe_get_pid(proc)
    return (exe, pid)
