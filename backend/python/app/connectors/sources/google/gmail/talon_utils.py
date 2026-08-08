import sys
import warnings

import chardet

# Talon imports cchardet, which does not support Python 3.12; chardet provides
# the compatible API Talon needs.
sys.modules["cchardet"] = chardet

# Talon's regex literals use unescaped backslashes (e.g. '\s', '\d') instead of
# raw strings, which trips Python 3.12+'s SyntaxWarning at import time. This is
# upstream noise we can't fix without patching the dependency, so silence it.
with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        message=r"invalid escape sequence.*",
        category=SyntaxWarning,
        module=r"^talon(?:\..+)?$",
    )
    from talon import quotations  # noqa: E402

quotations.register_xpath_extensions()
