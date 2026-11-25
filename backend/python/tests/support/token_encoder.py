"""A token count for a prompt's text: the OpenAI family's tokenizer when it is installed, else an estimate."""

from __future__ import annotations

from collections.abc import Callable, Sequence

Encode = Callable[[str], Sequence[int]]


def encoder() -> Encode:
    """The OpenAI family's tokenizer when it is installed, else about four characters a token."""
    try:
        import tiktoken

        enc = tiktoken.get_encoding("o200k_base")
        return lambda text: enc.encode(text, disallowed_special=())
    except Exception:  # any failure to load the tokenizer falls back to the estimate
        return lambda text: [hash(text[i : i + 4]) for i in range(0, len(text), 4)]


__all__ = ["Encode", "encoder"]
