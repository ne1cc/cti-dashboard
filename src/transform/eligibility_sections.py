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
_LEAD_WORDS = r"(?:No|None|Not|Any|Meets?|Fails?|Prior|Previous|Note|Without)\b"
_DISALLOWED_LEAD_RE = re.compile(
    rf"^\s*(?:{_BULLET_PATTERN})?\**\s*{_LEAD_WORDS}",
    re.IGNORECASE,
)

# Canonical qualifiers allowed before keywords on headings.
_CANONICAL_QUALIFIERS = (
    r"(?:Key|Other|Additional|Main|General|Specific|Primary|Secondary|Core|"
    r"Clinical|Angiographic|Ocular|Healthy|Common|Patient|Participant|Cohort|Part\s+[A-Za-z0-9]+)"
)

_COLON = "[:：]"
_NON_INCLUSION = r"non[-\s]*inclusions?"
_KEYWORD = rf"(?:{_NON_INCLUSION}|inclusions?|exclusions?)"

# Timing compounds such as 'Exclusion Criteria (post-inclusion):' name a study phase,
# not a second criteria list, so they do not count as mentioning that list.
_TEMPORAL_COMPOUND_RE = re.compile(r"\b(?:pre|post)[-\s]*(?:inclusions?|exclusions?)\b")

# Standalone heading lines: the whole line is the heading. Qualifier prefixes
# must end with a colon, and tails are limited to "for ..." phrases or parentheticals
# or colon-terminated descriptors. The lead-word lookahead keeps
# 'Medical staff: No exclusion criteria' from passing through the prefix path.
_HEADING_RE = re.compile(
    rf"""^\s*
    \**\s*
    (?:[A-Za-z][\w ()'&/,.-]{{0,40}}?{_COLON}\s*)?   # Prefix like 'Extension Phase:'
    (?!\**\s*{_LEAD_WORDS})
    (?:[A-Za-z][\w-]{{0,14}}\s+)?             # single-word qualifier
    \**\s*
    (?P<keyword>{_KEYWORD})\b
    \s*(?:\bcriteria\b)?\b
    (?:
        \s+for\b[^.!?,;:：]{{0,50}}
      | \s*\([^)!?,;]{{0,60}}\)
    )?
    (?:
        (?:\s*[,;:：-]?\s*[^.!?:：]{{0,80}})?{_COLON}
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
    (?:[A-Za-z][\w ()'&/,.-]{{0,40}}?{_COLON}\s*)?
    (?!\**\s*{_LEAD_WORDS})
    (?:{_CANONICAL_QUALIFIERS}\s+)?
    \**\s*
    (?P<keyword>{_KEYWORD})\b
    (?:
        \s+\bcriteria\b
        (?:\s*\([^)!?,;]{{0,60}}\))?
        \s*{_COLON}?
      |
        (?:\s*\bcriteria\b)?
        (?:\s*\([^)!?,;]{{0,60}}\))?
        \s*{_COLON}
    )
    \s*(?:\*\*)?\s*$""",
    re.IGNORECASE | re.VERBOSE,
)

# Inline headings: 'Inclusion Criteria: first criterion text...'. The colon
# must follow the keyword/criteria phrase (a parenthetical qualifier
# such as '(PCP)' may sit in between). Bold markers after the colon are not text.
_INLINE_HEADING_RE = re.compile(
    rf"""^\s*
    (?:{_BULLET_PATTERN})?
    \**\s*
    (?P<keyword>{_KEYWORD})\b
    \s*(?:\bcriteria\b)?\b
    (?:\s*\([^)!?,;]{{0,60}}\))?
    \**\s*
    {_COLON}[\s*]*(?P<rest>.+)$""",
    re.IGNORECASE | re.VERBOSE,
)

# Heading phrase appearing mid-line after criterion text
# ('... consent. Exclusion Criteria: The patients ...'). Requires sentence-ending
# punctuation ([.;:\)]) and mandatory word 'criteria' with colon, ensuring prose
# mentioning the bare words (e.g. '; Inclusion: allowed') is never split.
_MIDLINE_HEADING_RE = re.compile(
    rf"""(?<=[.;:：\)])\s+
    (?P<heading>
        \**\s*
        (?P<keyword>{_KEYWORD})\b
        \s+(?:criteria)\b
        (?:\s*\([^)!?,;]{{0,60}}\))?
        \**\s*
        {_COLON}[\s*]*
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# An exclusion or non-inclusion phrase that looks like a heading but is not an exact one:
# it ends the line ('6 months Exclusion Criteria'), takes a colon or a numbered item
# ('General Exclusion Criteria 2. History...'), or leads a short descriptor line
# ('Criteria for exclusion from the study were as follows:').
_NOT_INCLUSION = rf"(?:{_NON_INCLUSION}|exclusions?)"
_UNRECOGNIZED_HEADING_RE = re.compile(
    rf"""\b(?:
        {_NOT_INCLUSION}\s+criteri(?:a|on)\b
        (?:
            \**\s*(?:{_COLON}[\s*]*|$|(?=\d{{1,3}}\\?[.)]\s))
          | \s+(?:of|for)\b[^.!?:：]{{0,60}}{_COLON}\**\s*$
        )
      | criteria\s+for\s+(?:the\s+)?{_NOT_INCLUSION}\b[^.!?:：]{{0,60}}(?:{_COLON}\**)?\s*$
    )""",
    re.IGNORECASE | re.VERBOSE,
)
_SENTENCE_END_RE = re.compile(r"[.;:：)]\s+")

# Words before the phrase that make it a statement about the criteria, not a heading
# ('No exclusion criteria', 'does not meet any exclusion criteria', '(see exclusion criteria:').
_NOT_A_HEADING_RE = re.compile(
    r"\b(?:no|none|not|nor|neither|any|without|absence|lack|free|see|per|"
    r"meets?|meeting|met|fulfil\w*|satisf\w*)\b",
    re.IGNORECASE,
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


def _classify_heading_text(text: str) -> str:
    """Determine section kind.

    Falls back to unclassified for non-inclusion headings and for headings
    mentioning both lists, because neither can be asserted as one list.
    """
    lower = _TEMPORAL_COMPOUND_RE.sub(" ", text.lower())
    mentions_exclusion = re.search(r"\bexclusions?\b", lower) is not None
    mentions_inclusion = re.search(r"\binclusions?\b", lower) is not None
    if re.search(rf"\b{_NON_INCLUSION}\b", lower) or (mentions_exclusion and mentions_inclusion):
        return _KIND_UNCLASSIFIED
    if mentions_exclusion:
        return _KIND_EXCLUSION
    return _KIND_INCLUSION if mentions_inclusion else _KIND_UNCLASSIFIED


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
    return _classify_heading_text(heading_part), match.group("rest").strip(), heading_part


def _split_midline_heading(line: str) -> tuple[str, str, str] | None:
    """Split 'text. Exclusion Criteria: rest' into (text, heading, rest)."""
    match = _MIDLINE_HEADING_RE.search(line)
    if match is None:
        return None
    return line[: match.start()].strip(), match.group("heading").strip(), line[match.end() :]


def _split_unrecognized_heading(line: str) -> tuple[str, str, str] | None:
    """Split a heading-like line into (text before, heading, text after).

    The text before it stops at the last sentence end, so '... records. Exclusion
    Criteria' keeps its first sentence; otherwise the whole line is the heading.
    """
    body = _strip_bullet(line)
    match = _UNRECOGNIZED_HEADING_RE.search(body)
    if match is None:
        return None
    sentence_ends = list(_SENTENCE_END_RE.finditer(body, 0, match.start()))
    start = sentence_ends[-1].end() if sentence_ends else 0
    if _NOT_A_HEADING_RE.search(body, start, match.start()):
        return None
    marker_length = len(line) - len(body)
    before = line[: marker_length + start].strip() if start else ""
    return before, body[start : match.end()].strip(), body[match.end() :].strip()


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

    def _open_section(kind: str, heading: str) -> None:
        nonlocal current, found_heading
        builders.append(current)
        current = _SectionBuilder(kind, heading)
        if kind in (_KIND_INCLUSION, _KIND_EXCLUSION):
            found_heading = True

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        hk = _heading_kind(line)
        if hk is not None:
            _open_section(hk, _strip_bullet(line))
            continue

        inline = _inline_heading(line)
        if inline is not None:
            kind, rest, heading_part = inline
            _open_section(kind, _strip_bullet(heading_part))
            remainder = rest
        else:
            remainder = line

        while remainder:
            midline = _split_midline_heading(remainder)
            if midline is not None:
                before, heading, remainder = midline
                if before:
                    _add_content(before)
                _open_section(_classify_heading_text(heading), _strip_bullet(heading))
                continue
            closing = (
                _split_unrecognized_heading(remainder) if current.kind == _KIND_INCLUSION else None
            )
            if closing is None:
                _add_content(remainder)
                break
            # Not an exact heading, so nothing after it can be asserted as either list.
            before, heading, after = closing
            if before:
                _add_content(before)
            _open_section(_KIND_UNCLASSIFIED, heading)
            if after:
                current.start_item(after)
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
