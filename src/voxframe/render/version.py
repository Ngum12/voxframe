"""The renderer's version, kept apart so the plan can record it (D-147).

No imports: the scene plan and the segment cache both read this, and the
segment cache already imports the plan.
"""

from __future__ import annotations

__all__ = ["RENDERER_VERSION"]

#: Bumped when the renderer changes in a way that alters its output, or what it
#: expects of a plan.
#:
#: The segment cache keys on it, so a fix to the motion planner cannot leave
#: every cached segment stale and silently reuse the old behaviour. Every plan
#: records the version that made it, so a plan from an older renderer can be
#: detected and warned about (D-147).
#:
#: 2: still segments forced to square pixels (D-125).
#: 3: card text wrapped and centred line by line (D-145).
RENDERER_VERSION = 3
