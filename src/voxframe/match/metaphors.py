"""Visual metaphors for abstract speech (D-136).

A scene that says "why are you on earth, what is your goal" names nothing a
camera can photograph. This finds the themes it speaks about -- purpose, goals
-- and offers concrete images that stand for them: a summit, a finish line.

The metaphors live in a plain data file, ``metaphors.toml``, so a contributor
can add a theme or improve a query without touching code. The file is read
once and checked on load: a malformed entry fails loudly rather than silently
dropping a theme.

Two rules keep this honest:

- **Only for abstract scenes.** A scene whose search terms name something
  concrete is left alone: "coral reef" does not need a metaphor.
- **Never ahead of a literal match.** The matcher tries the scene's own words
  first and reaches for a metaphor only when that fails, and it records that
  the image is a metaphor, so the scene plan can say so.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from voxframe.match.queries import _normalise

__all__ = [
    "DEFAULT_PATH",
    "Theme",
    "is_abstract",
    "load_metaphors",
    "metaphor_queries",
    "themes_in",
]

#: The shipped dictionary.
DEFAULT_PATH = Path(__file__).with_name("metaphors.toml")

#: A scene counts as abstract when at least this share of its search terms'
#: words are abstract or theme words. Half, so "goal earth" qualifies and
#: "coral reef" does not.
ABSTRACT_SHARE = 0.5

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class Theme:
    """One abstract theme and the concrete images that stand for it."""

    name: str
    words: dict[str, frozenset[str]]
    queries: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Metaphors:
    """The loaded dictionary."""

    themes: tuple[Theme, ...]
    abstract: dict[str, frozenset[str]]

    def vocabulary(self, language: str) -> frozenset[str]:
        """Every word that is abstract, or names a theme, in a language."""
        words = set(self.abstract.get(language, frozenset()))
        for theme in self.themes:
            words |= theme.words.get(language, frozenset())
        return frozenset(words)


def _words(values: object, where: str) -> frozenset[str]:
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        raise ValueError(f"{where} must be a list of words")
    return frozenset(_normalise(value) for value in values)


@cache
def load_metaphors(path: Path = DEFAULT_PATH) -> Metaphors:
    """Read and check the dictionary.

    Raises:
        ValueError: A theme has no queries, or a field has the wrong shape. A
            contributor's typo should fail a test, not quietly remove a theme.
    """
    data = tomllib.loads(path.read_text(encoding="utf-8"))

    abstract = data.get("abstract", {})
    themes = []
    for name, entry in sorted(data.get("themes", {}).items()):
        queries = entry.get("queries")
        if not isinstance(queries, list) or not queries:
            raise ValueError(f"theme {name!r} needs at least one query")
        themes.append(
            Theme(
                name=name,
                words={
                    language: _words(entry.get(language, []), f"themes.{name}.{language}")
                    for language in ("en", "fr")
                },
                queries=tuple(str(query) for query in queries),
            )
        )

    return Metaphors(
        themes=tuple(themes),
        abstract={
            language: _words(abstract.get(language, []), f"abstract.{language}")
            for language in ("en", "fr")
        },
    )


def _tokens(text: str) -> list[str]:
    return [_normalise(word) for word in _WORD.findall(text)]


def themes_in(text: str, language: str, metaphors: Metaphors | None = None) -> list[Theme]:
    """Themes a piece of speech touches, in the order it first mentions them."""
    metaphors = metaphors or load_metaphors()
    language = language if language in ("en", "fr") else "en"
    order: dict[str, int] = {}
    for position, word in enumerate(_tokens(text)):
        for theme in metaphors.themes:
            if theme.name not in order and word in theme.words.get(language, ()):
                order[theme.name] = position
    by_name = {theme.name: theme for theme in metaphors.themes}
    return [by_name[name] for name in sorted(order, key=order.__getitem__)]


def is_abstract(
    queries: Sequence[str], language: str, metaphors: Metaphors | None = None
) -> bool:
    """Whether a scene's search terms are mostly words nothing can depict."""
    metaphors = metaphors or load_metaphors()
    language = language if language in ("en", "fr") else "en"
    words = [word for query in queries for word in _tokens(query)]
    if not words:
        return True
    vocabulary = metaphors.vocabulary(language)
    share = sum(1 for word in words if word in vocabulary) / len(words)
    return share >= ABSTRACT_SHARE


def metaphor_queries(
    text: str,
    queries: Sequence[str],
    language: str,
    *,
    limit: int = 3,
    metaphors: Metaphors | None = None,
) -> list[str]:
    """Concrete queries standing for an abstract scene's themes.

    Empty when the scene is concrete, or touches no known theme. Spread across
    themes before going deep into one: a scene about goals *and* faith gets one
    image idea for each rather than three for goals.
    """
    metaphors = metaphors or load_metaphors()
    if not is_abstract(queries, language, metaphors):
        return []
    themes = themes_in(text, language, metaphors)
    if not themes:
        return []

    chosen: list[str] = []
    depth = 0
    while len(chosen) < limit and depth < max(len(t.queries) for t in themes):
        for theme in themes:
            if depth < len(theme.queries) and len(chosen) < limit:
                chosen.append(theme.queries[depth])
        depth += 1
    return chosen
