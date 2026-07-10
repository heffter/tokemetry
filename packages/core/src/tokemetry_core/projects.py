r"""Project attribution: fold raw working-directory paths into project groups.

Claude Code records each event's ``cwd`` verbatim, so one logical project
fragments into many keys: case-variant drive letters (``c:\`` vs ``C:\``), git
worktrees under ``.claude/worktrees/<name>``, arbitrary subfolders, and Windows
8.3 short names. :func:`project_group` collapses these to a single stable label
using a root-marker heuristic, so the dashboard groups by project rather than
by directory.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

#: Default root markers: the segment following one of these is the project
#: name (e.g. ``.../devel/TheraCore/...`` -> ``TheraCore``).
DEFAULT_ROOTS: tuple[str, ...] = ("devel",)

#: Label for events with no recorded working directory.
UNATTRIBUTED = "(unattributed)"

# A ``.claude/worktrees/<name>`` segment and anything after it: a worktree is
# part of its parent project, not a project of its own.
_WORKTREE_RE = re.compile(r"[\\/]\.claude[\\/]worktrees[\\/][^\\/]+.*$", re.IGNORECASE)

# A bare drive letter such as ``C:`` (dropped so ``c:\`` and ``C:\`` agree).
_DRIVE_RE = re.compile(r"^[A-Za-z]:$")

_SEP_RE = re.compile(r"[\\/]+")


def project_group(path: str | None, roots: Iterable[str] = DEFAULT_ROOTS) -> str:
    """Return the project group for a raw working-directory path.

    The path is stripped of any ``.claude/worktrees/<name>`` tail, split on
    either separator, and its drive letter dropped. If a segment matches one of
    ``roots`` (case-insensitively), the group is the segment immediately after
    it; otherwise the group is the last path segment. An empty or missing path
    yields :data:`UNATTRIBUTED`. The original casing of the chosen segment is
    preserved so labels read naturally.
    """
    if path is None or not path.strip():
        return UNATTRIBUTED

    stripped = _WORKTREE_RE.sub("", path)
    segments = [seg for seg in _SEP_RE.split(stripped) if seg and seg != "."]
    if segments and _DRIVE_RE.match(segments[0]):
        segments = segments[1:]
    if not segments:
        return UNATTRIBUTED

    lowered_roots = {root.lower() for root in roots}
    for index, segment in enumerate(segments):
        if segment.lower() in lowered_roots and index + 1 < len(segments):
            return segments[index + 1]
    return segments[-1]
