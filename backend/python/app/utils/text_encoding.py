"""Encodings tried, in order, for text files that do not declare one."""

# utf-8-sig reads plain UTF-8 too and drops a byte-order mark (which would otherwise open the
# first CSV header). cp1252 comes before latin-1 because latin-1 decodes every byte, so nothing
# after it ever runs, and Windows files use 0x80-0x9F for quotes, dashes and the euro sign where
# latin-1 has control characters. latin-1 stays last: it accepts what cp1252 leaves undefined.
TEXT_FILE_ENCODINGS: tuple[str, ...] = ("utf-8-sig", "cp1252", "latin-1")
