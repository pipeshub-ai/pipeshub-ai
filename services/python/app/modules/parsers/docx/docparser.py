import subprocess
import os
import tempfile
import shutil
from io import BytesIO


class DocParser:
    """Parser for Microsoft Word .doc and .docx files"""

    def __init__(self):
        pass

    def convert_doc_to_docx(self, binary: bytes) -> BytesIO:
        """Convert .doc file to .docx using LibreOffice
        
        Args:
            binary (bytes): The binary content of the .doc file
            
        Returns:
            BytesIO: The converted .docx file content as a BytesIO stream
            
        Raises:
            subprocess.CalledProcessError: If LibreOffice is not installed
            Exception: If conversion fails
        """
        temp_dir = None
        temp_doc = None
        try:
            # Check if LibreOffice is installed
            subprocess.run(['which', 'libreoffice'], check=True)

            # Create temporary directory and file
            temp_dir = tempfile.mkdtemp()
            temp_doc = os.path.join(temp_dir, 'input.doc')
            
            # Write binary content to temporary file
            with open(temp_doc, 'wb') as f:
                f.write(binary)

            # Convert .doc to .docx using LibreOffice
            subprocess.run([
                'libreoffice',
                '--headless',
                '--convert-to', 'docx',
                '--outdir', temp_dir,
                temp_doc
            ], check=True, capture_output=True)

            # Get the docx file path
            docx_file = os.path.join(temp_dir, 'input.docx')

            if not os.path.exists(docx_file):
                raise Exception("DOCX conversion failed")

            # Read the converted file into BytesIO
            with open(docx_file, 'rb') as f:
                docx_content = BytesIO(f.read())
            
            return docx_content

        except subprocess.CalledProcessError:
            print("Error: 'libreoffice' is not installed. Please install it using:")
            print("sudo apt-get install libreoffice")
            raise
        except Exception as e:
            print(f"Error converting .doc to .docx: {e}")
            raise
        finally:
            # Clean up temporary files in all cases
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
