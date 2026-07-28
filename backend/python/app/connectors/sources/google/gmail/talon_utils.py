import sys

import chardet

# Talon imports cchardet, which does not support Python 3.12; chardet provides
# the compatible API Talon needs.
sys.modules["cchardet"] = chardet

from talon import quotations  # noqa: E402

quotations.register_xpath_extensions()
