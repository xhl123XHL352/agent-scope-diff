import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent_scope_diff.cli import GitError, changes, main


class ScopeDiffTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.git("init", "--quiet", "--initial-branch=main")
        self.git("config", "user.name", "Scope Fixture")
        self.git("config", "user.email", "scope@example.invalid")
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.root, check=True, capture_output=True,
        ).stdout

    def write(self, path, content):
        destination = self.root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")

    def commit(self):
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "fixture")

    def test_added_rule_reaches_unchanged_descendant_but_not_sibling(self):
        self.write("AGENTS.md", "Repository guidance\n")
        self.write("src/deep/tool.py", "pass\n")
        self.write("docs/intro.md", "# Introduction\n")
        self.commit()

        self.write("src/AGENTS.md", "Run unit tests\n")
        self.git("add", "src/AGENTS.md")
        result = {change.path: change for change in changes(self.root / "src" / "deep")}
        self.assertEqual(list(result), ["src/AGENTS.md", "src/deep/tool.py"])
        self.assertEqual(result["src/AGENTS.md"].status, "added")
        tool = result["src/deep/tool.py"]
        self.assertEqual(tool.status, "scope-changed")
        self.assertEqual([rule.path for rule in tool.before], ["AGENTS.md"])
        self.assertEqual([rule.path for rule in tool.after], ["AGENTS.md", "src/AGENTS.md"])

    def test_rule_changes_are_measured_in_index_and_identified_by_blob(self):
        self.write("AGENTS.md", "Original\n")
        self.write("src.py", "pass\n")
        self.commit()
        self.write("AGENTS.md", "Staged\n")
        self.git("add", "AGENTS.md")
        staged = self.git("rev-parse", ":AGENTS.md").decode().strip()
        self.write("AGENTS.md", "Unstaged\n")

        rule_change, source_change = changes(self.root)
        self.assertEqual(rule_change.status, "modified")
        self.assertEqual(source_change.status, "scope-changed")
        self.assertNotEqual(source_change.before[0].oid, staged)
        self.assertEqual(source_change.after[0].oid, staged)

    def test_removed_override_exposes_default_rule(self):
        self.write("AGENTS.override.md", "")
        self.write("AGENTS.md", "Default\n")
        self.write("app.py", "pass\n")
        self.commit()
        self.git("rm", "AGENTS.override.md")

        records = {change.path: change for change in changes(self.root)}
        self.assertEqual(records["AGENTS.override.md"].status, "deleted")
        self.assertIsNone(records["AGENTS.override.md"].after)
        self.assertEqual(records["app.py"].before[0].path, "AGENTS.override.md")
        self.assertEqual(records["app.py"].after[0].path, "AGENTS.md")

    def test_unborn_head_omits_intent_to_add(self):
        self.write("AGENTS.md", "Draft\n")
        self.write("my tool.py", "pass\n")
        self.git("add", "-N", "AGENTS.md")
        self.git("add", "my tool.py")

        (change,) = changes(self.root)
        self.assertEqual((change.path, change.status), ("my tool.py", "added"))
        self.assertIsNone(change.before)
        self.assertEqual(change.after, ())

    def test_unmerged_entries_are_reported(self):
        self.write("app.py", "original\n")
        self.commit()
        self.git("checkout", "--quiet", "-b", "other")
        self.write("app.py", "other change\n")
        self.commit()
        self.git("checkout", "--quiet", "main")
        self.write("app.py", "main change\n")
        self.commit()
        result = subprocess.run(
            ["git", "merge", "other"], cwd=self.root, capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        with self.assertRaisesRegex(GitError, "unmerged index entry"):
            changes(self.root)

    def test_json_mode_distinguishes_empty_chain_from_absent_file(self):
        self.write("app.py", "pass\n")
        self.git("add", "app.py")
        output = io.StringIO()
        with patch("pathlib.Path.cwd", return_value=self.root), contextlib.redirect_stdout(output):
            self.assertEqual(main(["--json"]), 0)
        self.assertEqual(json.loads(output.getvalue()), [
            {"path": "app.py", "status": "added", "before": None, "after": []},
        ])
