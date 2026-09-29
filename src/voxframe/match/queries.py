"""Extract short visual queries from scene text.

Narration is not a search query. A sentence like "and so what I want to talk
about today is really the way that coral reefs are changing" embeds mostly
function words, and its CLIP vector lands near other conversational sentences
rather than near pictures of coral. Extracting ``coral reefs`` gives the image
encoder something it can actually match.

This runs fully offline with no LLM (project owner's instruction). It uses
stopword filtering, part-of-speech heuristics based on suffixes and
capitalisation, and adjacency rules to build noun phrases. That is less capable
than a tagger or an LLM, but it needs no model download, adds no latency, and
its failures are inspectable — the extracted queries are stored in the scene
plan so a user can see and edit them.

Language support matches the pipeline: English and French, with a shared
fallback for anything else.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

__all__ = [
    "SUPPORTED_LANGUAGES",
    "VisualQuery",
    "extract_queries",
    "searchable_text",
    "strip_credit_names",
    "strip_web_addresses",
]

SUPPORTED_LANGUAGES = ("en", "fr")

#: Words that carry no visual meaning. Kept deliberately broad: a stopword that
#: slips through dilutes the query, while one wrongly removed is usually
#: recoverable from the remaining words.
_STOPWORDS: dict[str, frozenset[str]] = {
    "en": frozenset("""
        a an the and or but if then than that this these those of in on at to
        for with from by as is are was were be been being am do does did doing
        have has had having will would shall should can could may might must
        i you he she it we they me him her us them my your his its our their
        what which who whom whose when where why how all any both each few more
        most other some such no nor not only own same so too very just about
        into over under again further once here there also because while during
        before after above below between through up down out off again really
        actually basically literally going want talk say said know think thing
        things way ways lot lots kind sort like well now today get got make
        made take took come came go went one two three first second next last
        need needs needed able unable ought gonna wanna gotta let lets cannot
        don doesn didn won wouldn couldn shouldn isn aren wasn weren hasn
        haven hadn ain whereby thereby within without via per yet ever even
        still much many every anything something nothing everything anyone
        someone everyone com org net www http https
        thy thee thou thine dost doth hath ye
        """.split()),
    "fr": frozenset("""
        le la les un une des du de d l et ou mais si alors que qui quoi dont
        ce cet cette ces celui celle ceux celles a à au aux en dans sur sous
        par pour avec sans vers chez est sont était étaient être été suis es
        ai as avons avez ont avait avaient sera seront serait fait faire fais
        je tu il elle nous vous ils elles me te se lui leur mon ma mes ton ta
        tes son sa ses notre nos votre vos leurs
        quand où pourquoi comment tout toute tous toutes autre autres même
        aussi très bien plus moins peu beaucoup encore déjà toujours jamais
        ne pas non oui donc car or ni puis ensuite enfin voilà voici cela ça
        chose choses façon manière genre sorte parler dire savoir penser
        aujourd hui maintenant vraiment
        ceci tel telle tels telles peut peux pouvez pouvons peuvent pouvait
        pourrait pouvoir doit dois devez devons doivent devait devrait devoir
        faut fallait falloir veut veux voulez voulons veulent voulait vouloir
        vais vas allons allez vont aller besoin capable com org www
        """.split()),
}

#: Suffixes that usually mark a concrete noun. Crude, but it runs offline in
#: microseconds and errs toward keeping words rather than dropping them.
_NOUN_SUFFIXES: dict[str, tuple[str, ...]] = {
    "en": ("tion", "ment", "ness", "ity", "ship", "ism", "ist", "er", "or",
           "age", "ure", "ance", "ence", "scape", "land", "house", "room"),
    "fr": ("tion", "ment", "té", "eur", "euse", "age", "ance", "ence", "ure",
           "isme", "iste", "erie", "aire"),
}

#: Adjectives worth keeping: they change what an image looks like. Colours and
#: scale words steer CLIP meaningfully; judgement words like "good" do not.
_VISUAL_MODIFIERS: dict[str, frozenset[str]] = {
    "en": frozenset("""
        red orange yellow green blue purple pink brown black white grey gray
        golden silver bright dark deep pale vivid warm cool
        large small huge tiny vast narrow wide tall short long round flat
        ancient modern old new young rural urban wild frozen burning
        """.split()),
    "fr": frozenset("""
        rouge orange jaune vert verte bleu bleue violet rose brun noir noire
        blanc blanche gris grise doré dorée argenté clair sombre profond pâle
        grand grande petit petite immense minuscule vaste étroit large haut
        ancien ancienne moderne vieux jeune rural urbain sauvage gelé brûlant
        """.split()),
}

#: Suffixes that usually mark a *verb*. A phrase ends before one of these:
#: "Amazon rainforest produces" should be "Amazon rainforest", because the verb
#: pulls the embedding toward action imagery rather than the subject.
_VERB_SUFFIXES: dict[str, tuple[str, ...]] = {
    # "-es" and "-s" are deliberately absent: they collide with plural nouns,
    # which are the commonest visual subjects in narration. Verbs ending that
    # way are named in _COMMON_VERBS instead.
    "en": ("ing", "ed", "ates", "ises", "izes"),
    "fr": ("aient", "aits", "ait", "ant", "ent", "era", "eront", "ées", "ée",
           "és", "er", "ir", "oir", "re", "ez"),
}

#: Verbs frequent enough to be worth naming outright, where suffix rules would
#: misfire. "produces" ends in -es but so does "clothes".
_COMMON_VERBS: dict[str, frozenset[str]] = {
    "en": frozenset("""
        produce produces produced walk walks walked run runs ran show shows
        showed change changes changed grow grows grew move moves moved
        become becomes became remain remains create creates created
        rose rise rises risen fell fall falls fallen came come comes
        went go goes gone stood stand stands saw see sees seen
        took take takes taken gave give gives given held hold holds
        rolled roll rolls covered cover covers spread stretched stretch
        identify identifies identified call calls called leave assist assists
        accomplish accomplishes accomplished execute executes executed help
        helps helped try tries tried use uses keep keeps kept seem seems
        seemed feel feels tell tells told ask asks asked mean means meant
        wish wishes read learn learns
        """.split()),
    "fr": frozenset("""
        produire produit produisent marcher marche marchait dominer domine
        dominait dominaient chanter chante chanté chantait tenir tient tenait
        trouver trouve trouva devenir devient restait avoir ayant être étant
        dit dis disait renseigner participer appartiennent appartient
        subsister prient prier prêter
        """.split()),
}

#: A word, keeping an internal apostrophe so contractions and elisions stay
#: whole until :func:`_resolve_contraction` decides what they mean. Splitting
#: first is what turned "don't" into the search term "don" (D-135).
_WORD = re.compile(r"[^\W\d_]+(?:['\u2019][^\W\d_]+)*", re.UNICODE)

#: Top-level domains a narrator reads out. Spoken forms ("dot com", "point
#: org") use only unambiguous ones: "point de vue" is French, not a domain.
_TLDS = (
    "com|org|net|edu|gov|mil|int|info|biz|io|co|uk|fr|be|ch|ca|de|eu|us|"
    "me|tv|app|dev|ai|ly|fm|ac|au|nz|in"
)
_SPOKEN_TLDS = "com|org|net|edu|gov|info|io|fr"

#: Web addresses and anything ending in a domain, in any language (D-139).
#: Transcribers write them several ways: "librivox.org", "Librevox .org",
#: "www.example.com", "https://...", "example dot com", "librivox point org".
#: The name before the domain goes too: "Librevox" is a website, not a
#: subject. A space is allowed before the dot, never after, so a sentence
#: break such as "la fin. De plus" is never read as a domain.
_WEB_ADDRESS = re.compile(
    r"\b(?:https?://|www\.)\S+"
    r"|\S+@\S+\.\w+"
    r"|(?:\b[\w-]+\s?)?(?:\.[\w-]+)*\s?\.(?:" + _TLDS + r")\b(?:/\S*)?"
    r"|\b[\w-]+\s+(?:dot|point)\s+(?:" + _SPOKEN_TLDS + r")\b",
    re.IGNORECASE | re.UNICODE,
)

#: Left where a web address was, so the words either side never join into one
#: phrase. Too short to be a content word, so it only ever ends a phrase.
_BREAK = "\x00"

#: A word, or the break left by a removed web address.
_TOKEN = re.compile(_WORD.pattern + "|" + _BREAK, re.UNICODE)


def strip_web_addresses(text: str, *, marker: str = _BREAK) -> str:
    """Remove web addresses and domain names from narration (D-139).

    A domain names a website, and a website is never what a scene should show:
    "read for Librevox .org" searched for "Librevox" and matched an image for
    it. The scene keeps its other words, or falls through to an atmospheric
    image.

    Args:
        text: Narration.
        marker: What stands where each address was. The default, a NUL, ends
            a phrase during query extraction; pass ``""`` for text that is
            embedded whole.
    """
    stripped = _WEB_ADDRESS.sub(f" {marker} ", text)
    return stripped if marker else " ".join(stripped.split())


#: A name as a transcriber writes one: capitalised words, joined by the small
#: words names contain ("Fox in the Stars", "Jean de la Fontaine"). It must end
#: on a capitalised word. "in" joins only as "in the": "Jane Smith in London"
#: is a reader and a place, and the place may be the subject.
_NAME = (
    r"[A-ZÀ-Ý][\w'\u2019-]*"
    r"(?:\s+(?:(?:in\s+the|the|of|and|de|du|des|la|le|van|von|da|di)\s+)*"
    r"[A-ZÀ-Ý][\w'\u2019-]*)*"
)

#: A reader credit and the name it credits (D-141): "read by", "recorded by",
#: "narrated by", "lu par", "enregistré par". LibriVox writes "read for
#: LibriVox.org by …", so an optional "for …" may sit between verb and "by";
#: whom it was read for is part of the credit, and goes with the name.
#: Only these patterns: a name elsewhere in a sentence may well be its subject.
_CREDIT = re.compile(
    r"(?i:\b(?:read|recorded|narrated|performed|voiced)"
    r"(?:\s+for\s+[^.,;:!?]*?)?\s+by"
    r"|\b(?:lu|lue|lus|enregistr[ée]e?s?|racont[ée]e?s?|narr[ée]e?s?|interpr[ée]t[ée]e?s?)"
    r"\s+par)"
    r"\s+" + _NAME,
    re.UNICODE,
)


def strip_credit_names(text: str, *, marker: str = _BREAK) -> str:
    """Remove reader credits and the names in them (D-141).

    "read for Librevox .org by Fox in the Stars" matched a photograph of a fox:
    the reader's name is not the scene's subject, and neither is whom it was
    read for. The whole credit goes, from its verb to the end of the name.
    """
    stripped = _CREDIT.sub(f" {marker} ", text)
    return stripped if marker else " ".join(stripped.split())


def searchable_text(text: str, *, marker: str = _BREAK) -> str:
    """Narration without what never belongs in a search: web addresses
    (D-139) and names in reader credits (D-141).

    Addresses go first and leave their break behind, so "read for Librevox
    .org by Fox" is still recognised as a credit once ".org" has gone.
    """
    stripped = strip_credit_names(strip_web_addresses(text))
    if marker == _BREAK:
        return stripped
    return " ".join(stripped.replace(_BREAK, marker).split())


#: English suffixes after an apostrophe that mark a negated auxiliary: the
#: whole token is a function word. "don't", "can't", "won't".
_NEGATED = frozenset({"t"})

#: French elided prefixes: "l'eau" is "eau", "qu'il" is "il".
_ELISIONS = frozenset(
    {"l", "d", "j", "m", "n", "s", "t", "c", "qu", "jusqu", "lorsqu", "puisqu"}
)


def _resolve_contraction(token: str, language: str) -> str | None:
    """The part of a contracted token that carries meaning, or ``None``.

    - English negated auxiliaries ("don't") are dropped whole.
    - Other English suffixes ("people's", "you're", "they'll") leave the base,
      which the stopword list then judges on its own: "people" stays, "you"
      goes.
    - French elisions ("l'eau", "qu'il") leave what follows the apostrophe.
    """
    parts = re.split("['\u2019]", token)
    if len(parts) == 1:
        return token

    head, tail = parts[0], parts[-1]
    if language == "fr":
        return tail if _normalise(head) in _ELISIONS else token
    if _normalise(tail) in _NEGATED:
        return None
    return head


@dataclass(frozen=True, slots=True)
class VisualQuery:
    """A search phrase extracted from a scene, with its confidence.

    Attributes:
        text: The phrase to embed, e.g. ``"coral reef"``.
        weight: Relative importance, 0-1. Longer noun phrases and
            earlier-appearing terms score higher.
        source_words: Indices into the scene's words, so a user editing the
            plan can see where a query came from.
    """

    text: str
    weight: float = 1.0
    source_words: tuple[int, ...] = ()


def _normalise(word: str) -> str:
    """Lowercase and strip accents for stopword comparison.

    Accent stripping matters for French: ``À`` and ``a`` must compare equal to
    the stopword list, and transcripts vary in how they accent capitals.
    """
    lowered = word.lower()
    decomposed = unicodedata.normalize("NFD", lowered)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def _looks_like_noun(word: str, language: str) -> bool:
    """Heuristic test for a concrete noun.

    Capitalised mid-sentence words are treated as proper nouns, which are
    usually the most visually specific terms in a sentence.
    """
    if len(word) < 3:
        return False

    normalised = _normalise(word)
    suffixes = _NOUN_SUFFIXES.get(language, _NOUN_SUFFIXES["en"])

    if normalised.endswith(suffixes):
        return True

    # A capitalised word that is not sentence-initial is likely a proper noun.
    if word[0].isupper():
        return True

    # Plurals are usually nouns; "-s" alone is weak, so require some length.
    return bool(len(normalised) >= 5 and normalised.endswith("s") and not normalised.endswith("ss"))


def _is_content_word(word: str, language: str) -> bool:
    """Whether a word carries visual meaning."""
    normalised = _normalise(word)
    if len(normalised) < 3:
        return False
    stopwords = _STOPWORDS.get(language, _STOPWORDS["en"])
    return normalised not in stopwords


def _looks_like_verb(word: str, language: str) -> bool:
    """Heuristic test for a verb, which should end a noun phrase.

    Deliberately conservative. A false verb *drops the subject* — "waves" and
    "trees" were both being discarded because the ``-s``/``-es`` rule fires on
    plural nouns, which is exactly what narration uses for visual subjects.
    A false noun merely dilutes a query, which is the cheaper error.
    """
    normalised = _normalise(word)

    # An explicit list beats every heuristic below.
    verbs = _COMMON_VERBS.get(language, _COMMON_VERBS["en"])
    if normalised in {_normalise(v) for v in verbs}:
        return True

    if len(normalised) < 5:
        return False

    # A bare plural is a noun, not a verb. English third-person singular and
    # plural share the -s ending, and in narration the noun reading dominates:
    # "waves", "trees", "mountains", "clouds". Only -es preceded by a known
    # verb stem would indicate a verb, which the explicit list above covers.
    if language == "en" and normalised.endswith("s") and not normalised.endswith("ss"):
        return False

    suffixes = _VERB_SUFFIXES.get(language, _VERB_SUFFIXES["en"])
    if not normalised.endswith(suffixes):
        return False

    # A noun suffix beats a verb suffix: "building" and "painting" are nouns.
    noun_suffixes = _NOUN_SUFFIXES.get(language, _NOUN_SUFFIXES["en"])
    return not normalised.endswith(noun_suffixes)


def _is_modifier(word: str, language: str) -> bool:
    """Whether a word is an adjective worth keeping in a phrase."""
    modifiers = _VISUAL_MODIFIERS.get(language, _VISUAL_MODIFIERS["en"])
    return _normalise(word) in modifiers


def extract_queries(
    text: str,
    language: str = "en",
    *,
    max_queries: int = 3,
    max_words_per_query: int = 3,
) -> tuple[VisualQuery, ...]:
    """Extract short visual search phrases from scene text.

    Args:
        text: The scene's spoken text.
        language: Language code. Unsupported languages fall back to English
            rules, which still removes obvious noise.
        max_queries: How many phrases to return, best first.
        max_words_per_query: Longest phrase to build.

    Returns:
        Queries ordered by weight, highest first. Empty if the text holds no
        content words, which is legitimate for filler-only scenes.

    Example:
        >>> qs = extract_queries("Coral reefs are dying in warm oceans", "en")
        >>> qs[0].text
        'Coral reefs'
    """
    if language not in SUPPORTED_LANGUAGES:
        language = "en"

    tokens = [
        resolved
        for resolved in (
            token if token == _BREAK else _resolve_contraction(token, language)
            for token in _TOKEN.findall(searchable_text(text))
        )
        if resolved
    ]
    if not tokens:
        return ()

    # Build phrases: a run of modifiers followed by one or more nouns. This
    # keeps "warm ocean" and "coral reef" together rather than splitting them
    # into words that individually match the wrong images.
    phrases: list[tuple[list[str], list[int]]] = []
    current: list[str] = []
    current_indices: list[int] = []
    current_has_noun = False

    def flush() -> None:
        """Close the current phrase, keeping it only if it names something.

        A run of adjectives with no noun ("ancient", "warm") is not a useful
        query: it matches on mood rather than subject.
        """
        nonlocal current, current_indices, current_has_noun
        if current and current_has_noun:
            phrases.append((current, current_indices))
        current, current_indices, current_has_noun = [], [], False

    for index, token in enumerate(tokens):
        if not _is_content_word(token, language):
            flush()
            continue

        # Verbs terminate a phrase rather than joining it. Without this,
        # "Amazon rainforest produces" embeds the action, not the subject.
        if _looks_like_verb(token, language):
            flush()
            continue

        modifier = _is_modifier(token, language)
        noun_like = _looks_like_noun(token, language)

        # A content word that is neither a recognised noun nor a modifier is
        # still most likely a noun: the suffix rules only recognise a fraction
        # of English nouns, and "ocean", "forest" and "sun" all fall outside
        # them. Treating such a word as a noun is what lets it both extend and
        # start a phrase; without this, any subject the heuristics do not
        # recognise is silently dropped after a verb ends the previous phrase.
        probably_noun = noun_like or not modifier

        if len(current) < max_words_per_query:
            current.append(token)
            current_indices.append(index)
            current_has_noun = current_has_noun or probably_noun
            continue

        flush()
        current, current_indices = [token], [index]
        current_has_noun = probably_noun

    flush()

    if not phrases:
        # Nothing matched the noun heuristics. Fall back to content words,
        # which is better than returning nothing and leaving the scene unmatched.
        # The fallback must apply the same verb filter as the main path, or a
        # scene whose nouns went unrecognised returns verbs as queries.
        content = [
            (t, i)
            for i, t in enumerate(tokens)
            if _is_content_word(t, language) and not _looks_like_verb(t, language)
        ]
        if not content:
            return ()
        phrases = [([t], [i]) for t, i in content[:max_queries]]

    scored: list[VisualQuery] = []
    total = len(tokens)

    for words, indices in phrases:
        # Longer phrases are more specific and score higher. Earlier terms score
        # higher too: speakers usually name the subject before elaborating.
        length_score = min(len(words) / max_words_per_query, 1.0)
        position_score = 1.0 - (indices[0] / total) * 0.4
        weight = round(min(1.0, 0.5 * length_score + 0.5 * position_score), 3)

        scored.append(
            VisualQuery(
                text=" ".join(words),
                weight=weight,
                source_words=tuple(indices),
            )
        )

    # Drop near-duplicates: "coral reef" and "reef" would otherwise both rank.
    unique: list[VisualQuery] = []
    seen: set[str] = set()
    for query in sorted(scored, key=lambda q: -q.weight):
        key = _normalise(query.text)
        if any(key in other or other in key for other in seen):
            continue
        seen.add(key)
        unique.append(query)
        if len(unique) >= max_queries:
            break

    return tuple(unique)
