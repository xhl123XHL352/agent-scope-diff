# agent-scope-diff

Preview how a staged Git change affects [AGENTS.md](https://agents.md/) scopes. A
change to an instruction file can reach paths whose content stays the same.
This CLI compares the instruction chain for each affected path in `HEAD` and
the staged index.

## Install

Requires Python 3.10+ and Git 2.34+. The CLI has no Python runtime dependencies.

~~~sh
pipx install git+https://github.com/xhl123XHL352/agent-scope-diff.git
~~~

## Try it

From a Git repository that tracks `src/tool.py`, run:

~~~sh
printf '# Tools\n\nRun unittest before committing.\n' > src/AGENTS.md
git add src/AGENTS.md
agent-scope-diff
~~~

The report lists `src/AGENTS.md` as `added` and `src/tool.py` as
`scope-changed`. Each chain lists the instruction path and Git blob ID, so
edits to an instruction file show up even when its path stays the same.

~~~sh
agent-scope-diff --json
~~~

JSON emits an array sorted by Git path. Each entry has a `path`, `status`,
and `before` and `after` instruction chains. An absent path has a `null` chain.
An existing path with no instructions has an empty array.

## Scope rules

- Walk from the repository root to each tracked path's parent directory.
- Use the tracked regular `AGENTS.override.md` in a directory when present.
  Otherwise use the tracked regular `AGENTS.md`. This applies to empty files.
- Read Git metadata for `HEAD` and staged changes. Unstaged edits are ignored.
  With an unborn `HEAD` the comparison starts from an empty tree.
- Report renames as a deletion and an addition. Inspect submodules separately.
- Exit `0` for a completed report or `2` for errors such as merge conflicts.

## Tests

~~~sh
python -m unittest discover -s tests -v
~~~
