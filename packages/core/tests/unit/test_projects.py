"""Unit tests for project-group normalization."""

import pytest
from tokemetry_core.projects import UNATTRIBUTED, project_group


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        # Plain project directories under the root marker.
        (r"C:\devel\SonixPulse", "SonixPulse"),
        # Case-variant drive letter folds to the same group.
        (r"c:\devel\SonixPulse", "SonixPulse"),
        # A worktree is part of its parent project.
        (
            r"C:\devel\TheraCore\.claude\worktrees\pump-flow-range-endpoint-inversion",
            "TheraCore",
        ),
        # Worktree plus trailing subfolders still resolve to the project.
        (
            r"C:\devel\tokemetry\.claude\worktrees\phase-0-scaffold\apps\dashboard",
            "tokemetry",
        ),
        # Non-worktree subfolders fold into the project.
        (r"C:\devel\tokemetry\apps\dashboard", "tokemetry"),
        (r"C:\devel\tokemetry", "tokemetry"),
        # Deeply nested worktree subpath.
        (
            r"C:\devel\TheraVision\.claude\worktrees\fix-tvn\sw\MedFrame_II\Support Files",
            "TheraVision",
        ),
        # Distinct sibling folders stay distinct (root-folder heuristic).
        (r"C:\devel\TheraVision_Source_Code_Firmware", "TheraVision_Source_Code_Firmware"),
        # Paths without a root marker fall back to the basename; 8.3 short
        # names in middle segments do not affect the result.
        (r"C:\Users\cburdette\AppData\Local\Temp", "Temp"),
        (r"C:\Users\CBURDE~1\AppData\Local\Temp", "Temp"),
        # POSIX separators are handled too.
        ("/home/tamas/devel/tokemetry", "tokemetry"),
        # Empty and whitespace-only are unattributed.
        ("", UNATTRIBUTED),
        ("   ", UNATTRIBUTED),
    ],
)
def test_project_group(path: str, expected: str) -> None:
    assert project_group(path) == expected


def test_none_is_unattributed() -> None:
    assert project_group(None) == UNATTRIBUTED


def test_custom_root_markers() -> None:
    assert project_group(r"D:\src\myapp\lib", roots=("src",)) == "myapp"
    # "devel" is no longer a marker, so the basename is used.
    assert project_group(r"C:\devel\thing", roots=("src",)) == "thing"


def test_case_variants_merge() -> None:
    assert project_group(r"c:\devel\SonixPulse") == project_group(r"C:\devel\SonixPulse")
