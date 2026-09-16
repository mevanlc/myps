# myps

A cross-platform process tree viewer for the current user, with filtering.

`myps` lists only the processes owned by your user account, arranges them as a
tree, and renders each one as `name pid <exe> cmdline` with color. Noisy system
processes can be filtered out permanently via a config file, and a pattern
argument narrows the output further for one-off searches.

```
launchd 1 </sbin/launchd> ⛔️
  ⤷ kitty 1806 /Applications/kitty.app/Contents/MacOS/kitty
    ⤷ login 906 </usr/bin/login> 🪦
      ⤷ zsh 907 </bin/zsh> -zsh
      login 1809 </usr/bin/login> 🪦
      ⤷ zsh 1810 </bin/zsh> -zsh
    Terminal 13038 /System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal
    ⤷ login 22506 </usr/bin/login> 🪦
```

The `<...>` field appears only when the executable path differs from the
process name or from `argv[0]`, so it stays out of the way for ordinary
processes. Processes whose details can't be read are marked inline rather than
dropped: ⛔️ access denied, 🧟 zombie, 🪦 no longer exists.

`↥` after a PID indicates incomplete ancestry: red when that process's parent
PID could not be read or its parent could not be fetched, yellow when the failed
lookup belongs to an ancestor. The marker remains in plain output, without the
color distinction. Normal roots and deliberately excluded parents are unmarked.
Parent links are collected once and reused to build the tree; other process
details remain best-effort reads as processes start and exit.

## Requirements

- Python >= 3.11 (development uses the version in `.python-version`)
- [`uv`](https://docs.astral.sh/uv/)

Linux, macOS, and Windows are supported.

## Install

```bash
uv tool install git+https://github.com/mevanlc/myps-py.git
```

The same command works in PowerShell on Windows.

Or from a clone, for development:

```bash
git clone https://github.com/mevanlc/myps-py.git
cd myps-py
uv sync
uv run myps
```

## Usage

```
usage: myps [-h] [-b] [-f] [-r] [-C] [-v] [-k] [-K] [--include-self]
            [--color {always,auto,never}] [-c FILE | --no-config]
            [--init-config]
            [PATTERN]
```

| Option | Description |
| --- | --- |
| `PATTERN` | Filter processes by pattern (glob by default) |
| `-b`, `--bare` | Print a bare list of PIDs instead of the process tree |
| `-r`, `--regex` | Interpret `PATTERN` as a regular expression |
| `-C`, `--case` | Case-sensitive matching (default is case-insensitive) |
| `-k`, `--keep-ancestors` | Also show the ancestors of matching processes |
| `-K`, `--keep-children` | Also show the full subprocess trees of matching processes |
| `--include-self` | Include the running `myps` invocation and its descendants (excluded by default) |
| `-f`, `--full` | Disable truncation to terminal width |
| `--color {always,auto,never}` | Control colored output (default: `auto`) |
| `-c`, `--config FILE` | Read config from `FILE` instead of the default path |
| `--no-config` | Do not read a config file |
| `--init-config` | Write an example config to the target path and exit |
| `-v`, `--verbose` | Print argument and match diagnostics |

`PATTERN` is matched against the whole rendered line — name, pid, exe path, and
command line — so `myps python` finds a process whether "python" appears in its
name or only in its arguments.

Without `-k` or `-K`, matching lines are printed on their own. With `-k`, each
match is shown along with its chain of parents, so you can see where it sits in
the tree. With `-K`, each directly matching process is shown with its full
subprocess tree. When the flags are combined, ancestors added by `-k` do not
have their other subprocesses added by `-K`.

Output is truncated to the terminal width and colorized only when stdout is a
TTY; piping to `less` or `head` gives full, plain lines automatically.

```bash
myps                       # whole tree for your user
myps 'python*'             # glob match
myps -r 'node|deno'        # regex match
myps -k -r 'ssh-agent'     # match plus its ancestors
myps -K chrome             # matches plus their subprocess trees
myps -k --include-self myps # include myps itself in the match
myps -b 'python*'            # bare list of PIDs matching python
myps -f node | less -R     # untruncated, piped
```

## Configuration

Config lives at `~/.config/myps/config.toml` on Linux and macOS, and at
`%LOCALAPPDATA%\myps\config.toml` on Windows (override with `-c`, or disable
config loading with `--no-config`). Write a starting point with:

```bash
myps --init-config
```

The file defines two tables of regexes, matched against each process's
**executable path**. A process is dropped if it matches `regexSkipPatterns`,
unless it also matches `regexKeepPatterns` — so keep patterns carve exceptions
out of broad skip rules. Keys are descriptive labels only and are otherwise
ignored.

```toml
# Example myps configuration
[regexSkipPatterns]
system = '^/System/'
usr_sbin = '^/usr/sbin/'
usr_libexec = '^/usr/libexec/'
applications = '^/Applications/'
library = '^/Library/'
httpd = '/httpd$'
php_fpm = '/php-fpm$'

[regexKeepPatterns]
iterm = '/Applications/iTerm2?.app/'
terminal = '/Applications/Utilities/Terminal.app/'
xcode = '[Xx][Cc]ode'
```

Skipping applies before the tree is built, but parents of surviving processes
are re-attached so filtered output keeps its structure. This fetches one layer
of missing parents; `-k` follows the ancestor links collected in that tree.
Ancestors beyond that fetch scope are not marked as failed lookups. A missing
config file is not an error — it just means no processes are skipped.

## Development

```bash
uv run pytest        # tests
tasks/py_check       # ruff check
tasks/py_format      # ruff format
tasks/pre_commit     # fix imports, format, and check
```

Layout:

| Path | Purpose |
| --- | --- |
| `src/myps/cli.py` | Argument parsing, process collection, filtering, output |
| `src/myps/pstree.py` | `PSTree` — parent/child graph, roots, ancestor lookup |
| `src/myps/psprinter.py` | Tree rendering and per-process `rich` styling |
| `src/myps/pssafe.py` | `psutil` accessors that degrade to markers instead of raising |
| `src/myps/configutil.py` | Config path resolution, loading, sample writing |
| `src/myps/psutilutil.py` | Standalone helper for dumping process data (see `test/procdump.json`) |
