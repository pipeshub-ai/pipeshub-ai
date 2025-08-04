import os
import subprocess
import tempfile


class XLSParser:
    """Parser for Microsoft Excel .xls files"""

    def __init__(self) -> None:
        pass

    def convert_xls_to_xlsx(self, binary: bytes) -> bytes:
        """
        Convert .xls file to .xlsx using LibreOffice and return the xlsx binary content

        Args:
            binary (bytes): The binary content of the XLS file

        Returns:
            bytes: The binary content of the converted XLSX file

        Raises:
            subprocess.CalledProcessError: If LibreOffice is not installed or conversion fails
            FileNotFoundError: If the converted file is not found
            Exception: For other conversion errors
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                # Check if LibreOffice is installed
                subprocess.run(
                    ["which", "libreoffice"], check=True, capture_output=True
                )

                # Create input file path
                temp_input = os.path.join(temp_dir, "input.xls")

                # Write binary data to temporary file
                with open(temp_input, "wb") as f:
                    f.write(binary)

                # Convert .xls to .xlsx using LibreOffice
                subprocess.run(
                    [
                        "libreoffice",
                        "--headless",
                        "--convert-to",
                        "xlsx",
                        "--outdir",
                        temp_dir,
                        temp_input,
                    ],
                    check=True,
                    capture_output=True,
                    timeout=60,
                )

                # Get the xlsx file path
                xlsx_file = os.path.join(temp_dir, "input.xlsx")

                if not os.path.exists(xlsx_file):
                    raise FileNotFoundError(
                        "XLSX conversion failed - output file not found"
                    )

                # Read the converted file as binary
                with open(xlsx_file, "rb") as f:
                    xlsx_binary = f.read()

                return xlsx_binary

            except subprocess.CalledProcessError as e:
                error_msg = "LibreOffice is not installed. Please install it using: sudo apt-get install libreoffice"
                if e.stderr:
                    error_msg += (
                        f"\nError details: {e.stderr.decode('utf-8', errors='replace')}"
                    )
                raise subprocess.CalledProcessError(
                    e.returncode, e.cmd, output=e.output, stderr=error_msg.encode()
                )
            except subprocess.TimeoutExpired as e:
                raise Exception(
                    "LibreOffice conversion timed out after 30 seconds"
                ) from e
            except Exception as e:
                raise Exception(f"Error converting .xls to .xlsx: {str(e)}") from e
