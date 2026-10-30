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
_BULLET_PATTERN = r"(?:[-*•·‣◦–—]\s+)|(?:\(?\d{1,3}[.)]\s+)|(?:\(\d{1,3}\)\s+)"
_BULLET_RE = re.compile(rf"^\s*(?P<marker>{_BULLET_PATTERN})")

# Negative, lead-in, and note words that indicate a criterion or preamble
# rather than a section heading (e.g. '* No exclusion criteria', 'Meets inclusion criteria...').
_DISALLOWED_LEAD_RE = re.compile(
    rf"^\s*(?:{_BULLET_PATTERN})?\**\s*(?:No|None|Not|Meets?|Fails?|Prior|Previous|Note|Without)\b",
    re.IGNORECASE,
)

# Canonical qualifiers allowed before keywords on headings.
_CANONICAL_QUALIFIERS = (
    r"(?:Key|Other|Additional|Main|General|Specific|Primary|Secondary|Core|"
    r"Clinical|Angiographic|Ocular|Healthy|Common|Patient|Participant|Cohort|Part\s+[A-Za-z0-9]+)"
)

# Combined headings mentioning both inclusion and exclusion
# (e.g. 'Inclusion and Exclusion Criteria:').
_COMBINED_HEADING_RE = re.compile(
    r"\b(?:inclusions?[\s/&]+(?:and|or)?[\s/&]*exclusions?|exclusions?[\s/&]+(?:and|or)?[\s/&]*inclusions?)\b",
    re.IGNORECASE,
)

# Standalone heading lines: the whole line is the heading. Qualifier prefixes
# must end with a colon, and tails are limited to "for ..." phrases or parentheticals
# or colon-terminated descriptors.
_HEADING_RE = re.compile(
    r"""^\s*
    \**\s*
    (?:[A-Za-z][\w ()'&/,.-]{0,40}?:\s*)?   # Prefix like 'Extension Phase:'
    (?:[A-Za-z][\w-]{0,14}\s+)?             # single-word qualifier
    \**\s*
    (?P<keyword>(?:non[- ]?)?(?:inclusions?|exclusions?))\b
    \s*(?:\bcriteria\b)?\b
    (?:
        \s+for\b[^.!?,;:]{0,50}
      | \s*\([^)!?,;]{0,60}\)
    )?
    (?:
        (?:\s*[,;:-]?\s*[^.!?:]{0,80})?:
      | \s*$
    )
    \s*(?:\*\*)?\s*$""",
    re.IGNORECASE | re.VERBOSE,
)

# Standalone headings on bulleted lines. When colon-less, the qualifier MUST come from
# the canonical allowlist and the word 'criteria' is mandatory (e.g. '* INCLUSION CRITERIA'),
# rejecting bare '* Exclusion' or criteria lines with free tails.
_BULLETED_HEADING_RE = re.compile(
    rf"""^\s*
    (?:{_BULLET_PATTERN})?
    \**\s*
    (?:[A-Za-z][\w ()'&/,.-]{{0,40}}?:\s*)?
    (?:{_CANONICAL_QUALIFIERS}\s+)?
    \**\s*
    (?P<keyword>(?:non[- ]?)?(?:inclusions?|exclusions?))\b
    (?:
        \s+\bcriteria\b
        (?:\s*\([^)!?,;]{{0,60}}\))?
        \s*:?
      |
        (?:\s*\bcriteria\b)?
        (?:\s*\([^)!?,;]{{0,60}}\))?
        \s*:
    )
    \s*(?:\*\*)?\s*$""",
    re.IGNORECASE | re.VERBOSE,
)

# Inline headings: 'Inclusion Criteria: first criterion text...'. The colon
# must follow the keyword/criteria phrase (a parenthetical qualifier
# such as '(PCP)' may sit in between). Trailing bold markers are stripped.
_INLINE_HEADING_RE = re.compile(
    rf"""^\s*
    (?:{_BULLET_PATTERN})?
    \**\s*
    (?P<keyword>(?:non[- ]?)?(?:inclusions?|exclusions?))\b
    \s*(?:\bcriteria\b)?\b
    (?:\s*\([^)!?,;]{{0,60}}\))?
    \**\s*
    :(?:\**\s*)?(?P<rest>.+)$""",
    re.IGNORECASE | re.VERBOSE,
)

# Heading phrase appearing mid-line after criterion text
# ('... consent. Exclusion Criteria: The patients ...'). Requires sentence-ending
# punctuation ([.;:\)]) and mandatory word 'criteria' with colon, ensuring prose
# mentioning the bare words (e.g. '; Inclusion: allowed') is never split.
_MIDLINE_HEADING_RE = re.compile(
    r"""(?<=[.;:\)])\s+
    (?P<heading>
        \**\s*
        (?P<keyword>(?:non[- ]?)?(?:inclusions?|exclusions?))\b
        \s+(?:criteria)\b
        (?:\s*\([^)!?,;]{0,60}\))?
        \**\s*
        :
    )""",
    re.IGNORECASE | re.VERBOSE,
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


def _classify_heading_text(text: str) -> str | None:
    """Determine section kind.

    Falls back to unclassified for combined or non-inclusion headings.
    """
    if _COMBINED_HEADING_RE.search(text):
        return _KIND_UNCLASSIFIED
    lower = text.lower()
    if re.search(r"\bnon[- ]?inclusions?\b", lower):
        return _KIND_UNCLASSIFIED
    if re.search(r"\bexclusions?\b", lower):
        return _KIND_EXCLUSION
    if re.search(r"\binclusions?\b", lower):
        return _KIND_INCLUSION
    return None


def _heading_kind(line: str) -> str | None:
    """Return the section kind when the whole line is a recognized heading."""
    if _DISALLOWED_LEAD_RE.match(line):
        return None
    bulleted = _BULLET_RE.match(line) is not None
    regex = _BULLETED_HEADING_RE if bulleted else _HEADING_RE
    match = regex.match(line)
    if match is None:
        return None
    return _classify_heading_text(line)


def _inline_heading(line: str) -> tuple[str, str, str] | None:
    """Return (kind, rest-of-line, heading-portion) for a heading with inline text."""
    if _DISALLOWED_LEAD_RE.match(line):
        return None
    match = _INLINE_HEADING_RE.match(line)
    if match is None:
        return None
    heading_part = line[: match.start("rest")].rstrip()
    kind = _classify_heading_text(heading_part)
    if kind is None:
        return None
    rest = match.group("rest").strip()
    if rest.startswith("**"):
        rest = rest.lstrip("*").strip()
    return kind, rest, heading_part


def _split_midline_heading(line: str) -> tuple[str, str] | None:
    """Split 'text Exclusion Criteria: rest' at a mid-line heading phrase."""
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

        hk = _heading_kind(line)
        if hk is not None:
            builders.append(current)
            current = _SectionBuilder(hk, _strip_bullet(line))
            if hk in (_KIND_INCLUSION, _KIND_EXCLUSION):
                found_heading = True
            continue

        inline = _inline_heading(line)
        if inline is not None:
            kind, rest, heading_part = inline
            builders.append(current)
            current = _SectionBuilder(kind, _strip_bullet(heading_part))
            if kind in (_KIND_INCLUSION, _KIND_EXCLUSION):
                found_heading = True
            remainder = rest
        else:
            remainder = line

        while remainder:
            midline = _split_midline_heading(remainder)
            if midline is None:
                if remainder.strip():
                    _add_content(remainder.strip())
                break
            before, after = midline
            if before.strip():
                _add_content(before.strip())
            hk_after = _heading_kind(after)
            if hk_after is not None:
                builders.append(current)
                current = _SectionBuilder(hk_after, _strip_bullet(after))
                if hk_after in (_KIND_INCLUSION, _KIND_EXCLUSION):
                    found_heading = True
                break
            inline_after = _inline_heading(after)
            if inline_after is not None:
                kind2, rest2, heading_part2 = inline_after
                builders.append(current)
                current = _SectionBuilder(kind2, _strip_bullet(heading_part2))
                if kind2 in (_KIND_INCLUSION, _KIND_EXCLUSION):
                    found_heading = True
                remainder = rest2
                continue
            _add_content(after)
            break

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
