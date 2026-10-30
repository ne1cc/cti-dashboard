"""Lossless deterministic splitting of registry eligibility criteria text.

Splits ``eligibility_criteria_text`` blobs into Inclusion / Exclusion item
lists using only structural signals (section headings and bullet markers).
No eligibility interpretation, scoring, or paraphrasing happens here: item
text is preserved verbatim, and anything the splitter cannot confidently
classify is kept under ``unclassified`` so no text is ever dropped. The
verbatim source blob always remains available via ``criteria_text``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_KIND_INCLUSION = "inclusion"
_KIND_EXCLUSION = "exclusion"
_KIND_UNCLASSIFIED = "unclassified"

# Leading bullet markers that start a new item. Numbered markers require a
# separator plus whitespace so decimal doses ("1.5 mg") never split items.
_BULLET_RE = re.compile(
    r"^\s*(?P<marker>(?:[-*•·‣◦–—]\s+)|(?:\(?\d{1,3}[.)]\s+)|(?:\(\d{1,3}\)\s+))"
)

# Standalone heading lines: the whole line is the heading. Kept deliberately
# narrow — qualifier prefixes must end with a colon, and tails are limited to
# "for ..." phrases or parentheticals — so prose that merely mentions the
# words is never treated as a heading.
_HEADING_RE = re.compile(
    r"""^
    \**\s*                                  # optional bold markers
    (?:[A-Za-z][\w ()'&/,.-]{0,40}?:\s*)?   # 'Extension Phase:'-style prefix
    (?:[A-Za-z][\w-]{0,14}\s+)?             # single-word qualifier ('Key', 'General')
    \**\s*
    (?P<keyword>inclusions?|exclusions?)\b  # keyword
    \s*(?:\bcriteria\b)?\b                  # optional word 'criteria'
    (?:\s+for\b[^.!?,;:]{0,50}              # 'for Healthy Volunteers'
      |\s*\([^)!?,;]{0,60}\)                # '(applies to all patients)'
    )?
    (?:(?:\s*[,;:-]?\s*[^.!?:]{0,80})?:)?   # descriptor tail or bare colon (colon mandatory)
    \s*(?:\*\*)?\s*$""",
    re.IGNORECASE | re.VERBOSE,
)

# Standalone headings on bulleted lines get the same body but NOT the free
# descriptor tail: a bullet plus keyword plus arbitrary colon-terminated text
# is far more likely a criterion ("* Prior exclusion from study:") than a
# heading. "for ..."/parenthetical tails stay allowed (they are heading-shaped).
_BULLETED_HEADING_RE = re.compile(
    r"""^
    (?:[*•·‣◦]\s+|-\s+)?                    # optional bullet marker
    \**\s*
    (?:[A-Za-z][\w ()'&/,.-]{0,40}?:\s*)?   # 'Extension Phase:'-style prefix
    (?:[A-Za-z][\w-]{0,14}\s+)?             # single-word qualifier
    \**\s*
    (?P<keyword>inclusions?|exclusions?)\b
    \s*(?:\bcriteria\b)?\b
    (?:\s+for\b[^.!?,;:]{0,50}
      |\s*\([^)!?,;]{0,60}\)
    )?
    :?                                      # optional bare colon (no free tail)
    \s*(?:\*\*)?\s*$""",
    re.IGNORECASE | re.VERBOSE,
)

# Inline headings: 'Inclusion Criteria: first criterion text...'. The colon
# must directly follow the keyword/criteria phrase (a parenthetical qualifier
# such as '(PCP)' may sit in between), so any remaining text is
# unambiguously heading-owned content, not prose.
_INLINE_HEADING_RE = re.compile(
    r"""^
    (?:[*•·‣◦]\s+|-\s+|\*\*\s*)?
    \**\s*
    (?P<keyword>inclusions?|exclusions?)\b
    \s*(?:\bcriteria\b)?\b
    (?:\s*\([^)!?,;]{0,60}\))?
    \**\s*
    :(?P<rest>.+)$""",
    re.IGNORECASE | re.VERBOSE,
)

# Heading phrase appearing mid-line after criterion text
# ('... consent. Exclusion Criteria: The patients ...'). Requires a colon and
# sentence-ending punctuation plus title/all-caps casing so prose mentioning
# the words (e.g. '* No other exclusion:') is never split; the text before
# the phrase stays with the current section.
_MIDLINE_HEADING_RE = re.compile(
    r"""(?<=[.;:\)])\s+
    (?P<heading>
        \**\s*
        (?P<keyword>Inclusions?|Exclusions?|INCLUSIONS?|EXCLUSIONS?)\b
        \s*(?:\b(?:Criteria|CRITERIA)\b)?\b
        (?:\s*\([^)!?,;]{0,60}\))?
        \**\s*
        :
    )""",
    re.VERBOSE,
)


@dataclass(frozen=True)
class EligibilitySection:
    """One ordered chunk of the criteria blob.

    ``heading_raw`` is the verbatim heading line (bullet marker stripped) so
    ``reconstruct`` can rebuild the original text for lossless checks.
    """

    kind: str
    heading_raw: str | None
    items: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ParsedEligibilitySections:
    """Deterministic Inclusion/Exclusion split of one criteria blob.

    ``parsed`` is True when at least one Inclusion/Exclusion heading was
    recognized. When False, the entire text lives in ``unclassified``.
    """

    sections: list[EligibilitySection]
    parsed: bool

    def _items(self, kind: str) -> list[str]:
        return [item for section in self.sections if section.kind == kind for item in section.items]

    @property
    def inclusion(self) -> list[str]:
        return self._items(_KIND_INCLUSION)

    @property
    def exclusion(self) -> list[str]:
        return self._items(_KIND_EXCLUSION)

    @property
    def unclassified(self) -> list[str]:
        return self._items(_KIND_UNCLASSIFIED)


class _SectionBuilder:
    """Mutable accumulator for one section; flushed into a frozen section."""

    def __init__(self, kind: str, heading_raw: str | None) -> None:
        self.kind = kind
        self.heading_raw = heading_raw
        self.items: list[str] = []

    def start_item(self, text: str) -> None:
        self.items.append(text)

    def append_continuation(self, text: str) -> None:
        if self.items:
            self.items[-1] = f"{self.items[-1]}\n{text}"
        else:
            self.items.append(text)

    def build(self) -> EligibilitySection:
        return EligibilitySection(
            kind=self.kind, heading_raw=self.heading_raw, items=list(self.items)
        )


def _heading_kind(line: str) -> str | None:
    """Return the section kind when the whole line is a recognized heading.

    Bulleted lines are held to a stricter shape (no free descriptor tail) so a
    criterion that merely ends in a colon and contains the keyword
    ("* Prior exclusion from study:") is never read as a heading.
    """
    bulleted = _BULLET_RE.match(line) is not None
    regex = _BULLETED_HEADING_RE if bulleted else _HEADING_RE
    match = regex.match(line)
    if match is None:
        return None
    keyword = match.group("keyword").lower()
    return _KIND_EXCLUSION if keyword.startswith("exclusion") else _KIND_INCLUSION


def _inline_heading(line: str) -> tuple[str, str, str] | None:
    """Return (kind, rest-of-line, heading-portion) for a heading with inline text."""
    match = _INLINE_HEADING_RE.match(line)
    if match is None:
        return None
    keyword = match.group("keyword").lower()
    kind = _KIND_EXCLUSION if keyword.startswith("exclusion") else _KIND_INCLUSION
    return kind, match.group("rest").strip(), line[: match.start("rest")].rstrip()


def _split_midline_heading(line: str) -> tuple[str, str] | None:
    """Split 'text Exclusion Criteria: rest' at a mid-line heading phrase.

    Returns (text-before, heading-onwards) when a colon-terminated heading
    phrase occurs after leading content, else None. The colon requirement
    keeps prose that merely mentions the words unsplit.
    """
    match = _MIDLINE_HEADING_RE.search(line)
    if match is None or match.start() <= 0:
        return None
    return line[: match.start()].strip(), line[match.start("heading") :].strip()


def _strip_bullet(line: str) -> str:
    """Remove a leading bullet marker, preserving the remaining text."""
    match = _BULLET_RE.match(line)
    return line[match.end() :].strip() if match else line.strip()


def split_eligibility_sections(text: str | None) -> ParsedEligibilitySections | None:
    """Split a criteria blob into inclusion/exclusion/unclassified items.

    Returns None for missing or empty text. Never drops text: every
    non-blank source line lands in exactly one section, either as an item or
    as the verbatim ``heading_raw`` of the section it opens.
    """
    if text is None or not text.strip():
        return None

    builders: list[_SectionBuilder] = []
    current = _SectionBuilder(_KIND_UNCLASSIFIED, None)
    found_heading = False

    def _add_content(content_line: str) -> None:
        """Route a non-heading line into the current section (item or continuation)."""
        if _BULLET_RE.match(content_line):
            current.start_item(_strip_bullet(content_line))
        elif current.heading_raw is not None or current.items:
            current.append_continuation(content_line)
        else:
            current.start_item(content_line)

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # Whole-line headings win over the mid-line split, so prefix-style
        # headings ('Extension Phase: Inclusion Criteria:') stay intact.
        heading_kind = _heading_kind(line)
        if heading_kind is not None:
            builders.append(current)
            current = _SectionBuilder(heading_kind, _strip_bullet(line))
            found_heading = True
            continue

        # A heading phrase buried mid-line ('... consent. Exclusion Criteria: ...')
        # splits: the text before it stays with the current section, the
        # remainder re-enters this loop's logic as a heading line.
        midline = _split_midline_heading(line)
        if midline is not None:
            _add_content(midline[0])
            line = midline[1]

        # Check whole-line heading on the remainder first so end-of-line
        # headings without inline text (e.g. '... consent. Exclusion Criteria:')
        # open a new section rather than falling through to inline/content.
        heading_kind = _heading_kind(line)
        if heading_kind is not None:
            builders.append(current)
            current = _SectionBuilder(heading_kind, _strip_bullet(line))
            found_heading = True
            continue

        inline = _inline_heading(line)
        if inline is not None:
            kind, rest, heading_part = inline
            builders.append(current)
            current = _SectionBuilder(kind, _strip_bullet(heading_part))
            if rest:
                current.start_item(rest)
            found_heading = True
            continue

        _add_content(line)

    builders.append(current)

    sections = [
        builder.build() for builder in builders if builder.heading_raw is not None or builder.items
    ]
    return ParsedEligibilitySections(sections=sections, parsed=found_heading)


def reconstruct(parsed: ParsedEligibilitySections) -> str:
    """Rebuild the blob from sections (bullet markers and whitespace aside).

    Used by tests to prove losslessness: token-for-token, the reconstruction
    equals the original text with bullet markers stripped.
    """
    parts: list[str] = []
    for section in parsed.sections:
        if section.heading_raw is not None:
            parts.append(section.heading_raw)
        parts.extend(section.items)
    return "\n".join(parts)


def strip_bullet_markers(text: str) -> str:
    """Return the text with leading bullet markers removed from each line."""
    return "\n".join(_strip_bullet(line) for line in text.splitlines())
