import os
import subprocess
import tempfile


class PPTParser:
    """Parser for Microsoft PowerPoint .ppt files"""

    def __init__(self) -> None:
        pass

    def convert_ppt_to_pptx(self, binary: bytes) -> bytes:
        """Convert .ppt file to .pptx using LibreOffice

        Args:
            binary (bytes): The binary content of the .ppt file

        Returns:
            bytes: The converted .pptx file content as bytes

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
                temp_ppt = os.path.join(temp_dir, "input.ppt")

                # Write binary content to temporary file
                with open(temp_ppt, "wb") as f:
                    f.write(binary)

                # Convert .ppt to .pptx using LibreOffice
                subprocess.run(
                    [
                        "libreoffice",
                        "--headless",
                        "--convert-to",
                        "pptx",
                        "--outdir",
                        temp_dir,
                        temp_ppt,
                    ],
                    check=True,
                    capture_output=True,
                    timeout=60,
                )

                # Get the pptx file path
                pptx_file = os.path.join(temp_dir, "input.pptx")

                if not os.path.exists(pptx_file):
                    raise FileNotFoundError(
                        "PPTX conversion failed - output file not found"
                    )

                # Read the converted file into bytes
                with open(pptx_file, "rb") as f:
                    pptx_content = f.read()

                return pptx_content

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
                raise Exception(f"Error converting .ppt to .pptx: {str(e)}") from e
