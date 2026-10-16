"""Every extension the code parser has a grammar for is routed to it (P-3)."""
from app.config.constants.arangodb import CODE_FILE_EXTENSION_VALUES
from app.modules.parsers.code_parser.lang_config import SUPPORTED_CODE_EXTENSIONS


def test_the_code_path_takes_every_extension_the_parser_supports() -> None:
    assert SUPPORTED_CODE_EXTENSIONS <= CODE_FILE_EXTENSION_VALUES, (
        sorted(SUPPORTED_CODE_EXTENSIONS - CODE_FILE_EXTENSION_VALUES)
    )
