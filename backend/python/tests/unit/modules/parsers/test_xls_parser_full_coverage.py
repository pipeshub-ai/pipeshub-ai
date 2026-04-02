"""
Full coverage tests for app.modules.parsers.excel.xls_parser.XLSParser.

Targets the missing partial branch at line 73->77 where CalledProcessError
is raised with e.stderr being falsy (None or empty bytes).
"""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from app.modules.parsers.excel.xls_parser import XLSParser


class TestXLSParserCalledProcessErrorNoStderr:
    """Cover the branch where CalledProcessError has no stderr (falsy)."""

    @patch("app.modules.parsers.excel.xls_parser.subprocess.run")
    def test_called_process_error_with_none_stderr(self, mock_run):
        """CalledProcessError with stderr=None skips the error details append."""
        parser = XLSParser()

        error = subprocess.CalledProcessError(
            returncode=1,
            cmd=["which", "libreoffice"],
            output=b"",
            stderr=None,
        )
        mock_run.side_effect = error

        with pytest.raises(subprocess.CalledProcessError) as exc_info:
            parser.convert_xls_to_xlsx(b"fake xls binary")

        decoded_stderr = exc_info.value.stderr.decode("utf-8")
        assert "Error details" not in decoded_stderr
        assert "LibreOffice is not installed" in decoded_stderr

    @patch("app.modules.parsers.excel.xls_parser.subprocess.run")
    def test_called_process_error_with_empty_bytes_stderr(self, mock_run):
        """CalledProcessError with stderr=b'' (falsy) skips the error details append."""
        parser = XLSParser()

        error = subprocess.CalledProcessError(
            returncode=1,
            cmd=["libreoffice"],
            output=b"",
            stderr=b"",
        )
        mock_run.side_effect = error

        with pytest.raises(subprocess.CalledProcessError) as exc_info:
            parser.convert_xls_to_xlsx(b"fake xls binary")

        decoded_stderr = exc_info.value.stderr.decode("utf-8")
        assert "Error details" not in decoded_stderr
        assert "LibreOffice is not installed" in decoded_stderr

    @patch("app.modules.parsers.excel.xls_parser.subprocess.run")
    def test_called_process_error_with_stderr_present(self, mock_run):
        """CalledProcessError with non-empty stderr includes error details."""
        parser = XLSParser()

        error = subprocess.CalledProcessError(
            returncode=1,
            cmd=["libreoffice"],
            output=b"",
            stderr=b"some conversion error",
        )
        mock_run.side_effect = error

        with pytest.raises(subprocess.CalledProcessError) as exc_info:
            parser.convert_xls_to_xlsx(b"fake xls binary")

        decoded_stderr = exc_info.value.stderr.decode("utf-8")
        assert "Error details" in decoded_stderr
        assert "some conversion error" in decoded_stderr
