import argparse
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
import subprocess
import sys


@dataclass(frozen=True)
class Entry:
    mode: str
    oid: str


@dataclass(frozen=True)
class Instruction:
    path: str
    oid: str


@dataclass(frozen=True)
class Change:
    path: str
    status: str
    before: tuple[Instruction, ...] | None
    after: tuple[Instruction, ...] | None


class GitError(Exception):
    pass


def git(*args: str, cwd: Path, allow_missing: bool = False) -> bytes:
    result = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", *args],
        cwd=cwd,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        capture_output=True,
    )
    if allow_missing and result.returncode == 1 and not result.stderr:
        return b""
    if result.returncode:
        raise GitError(result.stderr.decode(errors="replace").strip() or "git failed")
    return result.stdout


def snapshots(cwd: Path) -> tuple[dict[str, Entry], dict[str, Entry]]:
    root = Path(os.fsdecode(git("rev-parse", "--show-toplevel", cwd=cwd).removesuffix(b"\n")))
    tree = git("rev-parse", "--verify", "--quiet", "HEAD^{tree}", cwd=root, allow_missing=True)
    tree_args = (tree.decode("ascii").strip(),) if tree else ()

    before = {}
    if tree_args:
        for record in git("ls-tree", "-r", "-z", *tree_args, cwd=root).split(b"\0")[:-1]:
            metadata, path = record.split(b"\t", 1)
            mode, _, oid = metadata.decode("ascii").split()
            before[os.fsdecode(path)] = Entry(mode, oid)

    after = before.copy()
    diff = git(
        "diff", "--cached", "--raw", "-z", "--no-abbrev", "--no-renames",
        "--no-ext-diff", "--ignore-submodules=none", "--ita-invisible-in-index",
        *tree_args, "--", cwd=root,
    )
    fields = diff.split(b"\0")[:-1]
    for metadata, path_bytes in zip(fields[::2], fields[1::2], strict=True):
        _, mode, _, oid, status = metadata.decode("ascii").split()
        path = os.fsdecode(path_bytes)
        if status == "U":
            raise GitError(f"resolve the unmerged index entry for {json.dumps(path)}")
        if status == "D":
            del after[path]
        else:
            after[path] = Entry(mode, oid)
    return before, after


def instruction_chain(entries: dict[str, Entry], directory: str) -> tuple[Instruction, ...]:
    chain = []
    while True:
        prefix = f"{directory}/" if directory else ""
        for filename in ("AGENTS.override.md", "AGENTS.md"):
            path = prefix + filename
            entry = entries.get(path)
            if entry is not None and entry.mode in ("100644", "100755"):
                chain.append(Instruction(path, entry.oid))
                break
        if not directory:
            return tuple(reversed(chain))
        directory = directory.rpartition("/")[0]


def changes(cwd: Path) -> list[Change]:
    before, after = snapshots(cwd)
    before_chains: dict[str, tuple[Instruction, ...]] = {}
    after_chains: dict[str, tuple[Instruction, ...]] = {}
    result = []
    for path in sorted(before.keys() | after.keys()):
        directory = path.rpartition("/")[0]
        if directory not in before_chains:
            before_chains[directory] = instruction_chain(before, directory)
        if directory not in after_chains:
            after_chains[directory] = instruction_chain(after, directory)
        old_chain = before_chains[directory] if path in before else None
        new_chain = after_chains[directory] if path in after else None
        if old_chain is None:
            status = "added"
        elif new_chain is None:
            status = "deleted"
        elif before[path] != after[path]:
            status = "modified"
        elif old_chain != new_chain:
            status = "scope-changed"
        else:
            continue
        result.append(Change(path, status, old_chain, new_chain))
    return result


def format_chain(chain: tuple[Instruction, ...] | None) -> str:
    if chain is None:
        return "(file absent)"
    if not chain:
        return "(no instructions)"
    return " > ".join(f"{json.dumps(item.path)}@{item.oid[:12]}" for item in chain)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-scope-diff",
        description="Compare AGENTS.md scope in HEAD and the staged Git index.",
    )
    parser.add_argument("--json", action="store_true", help="emit a JSON array")
    args = parser.parse_args(argv)
    try:
        result = changes(Path.cwd())
    except (GitError, OSError) as error:
        print(f"agent-scope-diff: {error}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps([asdict(change) for change in result], indent=2))
    elif result:
        for change in result:
            print(f"{change.status}  {json.dumps(change.path)}")
            print(f"  before  {format_chain(change.before)}")
            print(f"  after   {format_chain(change.after)}")
    else:
        print("No staged changes.")
    return 0
