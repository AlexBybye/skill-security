# Copyright 2026 Cisco Systems, Inc. and its affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for incremental pre-commit skill resolution."""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from skill_scanner.core.changed_skills import resolve_affected_skills
from skill_scanner.hooks.pre_commit import get_staged_files, main


def _make_skill(skill_dir: Path) -> Path:
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: test-skill\ndescription: test skill\n---\n# Test\n",
        encoding="utf-8",
    )
    return skill_dir


class TestResolveAffectedSkills:
    def test_resolves_relative_deleted_file(self, tmp_path):
        repo_root = tmp_path / "repo"
        skill_dir = _make_skill(repo_root / ".claude" / "skills" / "alpha")

        result = resolve_affected_skills(
            [".claude/skills/alpha/scripts/deleted.py"],
            repo_root=repo_root,
            skill_roots=(".claude/skills",),
        )

        assert result == {skill_dir.resolve()}

    def test_nearest_nested_skill_wins(self, tmp_path):
        repo_root = tmp_path / "repo"
        _make_skill(repo_root / "skills" / "outer")
        inner = _make_skill(repo_root / "skills" / "outer" / "nested")

        result = resolve_affected_skills(
            ["skills/outer/nested/scripts/run.py"],
            repo_root=repo_root,
            skill_roots=("skills",),
        )

        assert result == {inner.resolve()}

    def test_deduplicates_files_and_supports_spaces(self, tmp_path):
        repo_root = tmp_path / "repo"
        skill_dir = _make_skill(repo_root / "skills" / " skill with spaces ")

        result = resolve_affected_skills(
            [
                "skills/ skill with spaces /SKILL.md",
                "skills/ skill with spaces /scripts/run.py",
            ],
            repo_root=repo_root,
            skill_roots=("skills",),
        )

        assert result == {skill_dir.resolve()}

    def test_ignores_absolute_path_outside_repo(self, tmp_path):
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        outside = _make_skill(tmp_path / "outside")

        result = resolve_affected_skills(
            [outside / "scripts" / "run.py"],
            repo_root=repo_root,
        )

        assert result == set()

    def test_ignores_relative_path_traversal_outside_repo(self, tmp_path):
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        _make_skill(tmp_path / "outside")

        result = resolve_affected_skills(
            ["../outside/scripts/run.py"],
            repo_root=repo_root,
        )

        assert result == set()

    def test_ignores_configured_root_outside_repo(self, tmp_path):
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        outside = _make_skill(tmp_path / "outside" / "alpha")

        result = resolve_affected_skills(
            [repo_root / "unrelated" / "file.py"],
            repo_root=repo_root,
            skill_roots=(outside.parent,),
        )

        assert result == set()

    def test_rejects_skill_file_paths(self):
        with pytest.raises(ValueError, match="filename"):
            resolve_affected_skills([], skill_file="metadata/SKILL.md")


class TestStagedFileDiscovery:
    def test_includes_deleted_paths(self):
        completed = CompletedProcess(
            args=[],
            returncode=0,
            stdout="skills/alpha/deleted.py\n",
            stderr="",
        )

        with patch("skill_scanner.hooks.pre_commit.subprocess.run", return_value=completed) as run:
            assert get_staged_files() == ["skills/alpha/deleted.py"]

        assert run.call_args.args[0] == [
            "git",
            "diff",
            "--cached",
            "--name-only",
            "--diff-filter=ACMRD",
        ]


class TestPreCommitIncrementalFiles:
    def test_precommit_filenames_bypass_staged_diff(self, tmp_path, monkeypatch):
        repo_root = tmp_path / "repo"
        skill_dir = _make_skill(repo_root / ".claude" / "skills" / "alpha")
        monkeypatch.chdir(repo_root)

        rev_parse = CompletedProcess(
            args=["git", "rev-parse", "--show-toplevel"],
            returncode=0,
            stdout=f"{repo_root}\n",
            stderr="",
        )
        clean_result = {
            "skill_name": "alpha",
            "skill_directory": str(skill_dir),
            "findings": [],
        }

        with (
            patch("skill_scanner.hooks.pre_commit.subprocess.run", return_value=rev_parse),
            patch(
                "skill_scanner.hooks.pre_commit.get_staged_files",
                side_effect=AssertionError("staged diff must not run when filenames are provided"),
            ),
            patch("skill_scanner.hooks.pre_commit.scan_skill", return_value=clean_result) as scan,
        ):
            exit_code = main(
                [
                    ".claude/skills/alpha/SKILL.md",
                    ".claude/skills/alpha/scripts/run.py",
                ]
            )

        assert exit_code == 0
        scan.assert_called_once()
        assert scan.call_args.args[0] == skill_dir.resolve()

    def test_no_filenames_keeps_staged_fallback(self, tmp_path, monkeypatch):
        repo_root = tmp_path / "repo"
        skill_dir = _make_skill(repo_root / ".claude" / "skills" / "alpha")
        monkeypatch.chdir(repo_root)

        rev_parse = CompletedProcess(
            args=["git", "rev-parse", "--show-toplevel"],
            returncode=0,
            stdout=f"{repo_root}\n",
            stderr="",
        )
        clean_result = {
            "skill_name": "alpha",
            "skill_directory": str(skill_dir),
            "findings": [],
        }

        with (
            patch("skill_scanner.hooks.pre_commit.subprocess.run", return_value=rev_parse),
            patch(
                "skill_scanner.hooks.pre_commit.get_staged_files",
                return_value=[".claude/skills/alpha/scripts/run.py"],
            ) as staged,
            patch("skill_scanner.hooks.pre_commit.scan_skill", return_value=clean_result) as scan,
        ):
            exit_code = main([])

        assert exit_code == 0
        staged.assert_called_once_with()
        scan.assert_called_once()

    def test_install_command_remains_supported(self):
        with patch("skill_scanner.hooks.pre_commit.install_hook", return_value=0) as install:
            assert main(["install"]) == 0
        install.assert_called_once_with()
