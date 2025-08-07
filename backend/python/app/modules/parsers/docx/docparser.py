import os
import subprocess
import tempfile
from io import BytesIO


class DocParser:
    """Parser for Microsoft Word .doc and .docx files"""

    def __init__(self) -> None:
        pass

    def convert_doc_to_docx(self, binary: bytes) -> BytesIO:
        """Convert .doc file to .docx using LibreOffice

        Args:
            binary (bytes): The binary content of the .doc file

        Returns:
            BytesIO: The converted .docx file content as a BytesIO stream

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
                temp_doc = os.path.join(temp_dir, "input.doc")

                # Write binary content to temporary file
                with open(temp_doc, "wb") as f:
                    f.write(binary)

                # Convert .doc to .docx using LibreOffice
                subprocess.run(
                    [
                        "libreoffice",
                        "--headless",
                        "--convert-to",
                        "docx",
                        "--outdir",
                        temp_dir,
                        temp_doc,
                    ],
                    check=True,
                    capture_output=True,
                    timeout=60,
                )

                # Get the docx file path
                docx_file = os.path.join(temp_dir, "input.docx")

                if not os.path.exists(docx_file):
                    raise FileNotFoundError(
                        "DOCX conversion failed - output file not found"
                    )

                # Read the converted file into BytesIO
                with open(docx_file, "rb") as f:
                    docx_content = BytesIO(f.read())

                return docx_content

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
                raise Exception(f"Error converting .doc to .docx: {str(e)}") from e
