import re

# Mirrors TimeTagger's is_valid_tag_charcode(): letters, digits, '-', '_', '/'
# and any non-ASCII character. A '#' ends the current tag and starts a new one.
_TAG_RE = re.compile(r"#([0-9A-Za-z_/\-\u0080-\U0010ffff]+)")


def parse_tags(ds: str | None) -> list[str]:
    """Tags in order of first appearance, lowercased, without '#', de-duplicated."""
    seen: dict[str, None] = {}
    for match in _TAG_RE.finditer(ds or ""):
        seen.setdefault(match.group(1).lower(), None)
    return list(seen)
