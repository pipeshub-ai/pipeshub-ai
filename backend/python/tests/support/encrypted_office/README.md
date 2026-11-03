Password-protected Office files for the tests: `encrypted.docx`, `encrypted.xlsx`,
`encrypted.pptx` and the macro-enabled `encrypted.docm`, `encrypted.xlsm`, `encrypted.pptm`, each holding the text "Confidential salaries", encrypted
with the password `s3cret` (ECMA-376 agile encryption).

Made with msoffcrypto-tool (`msoffcrypto.format.ooxml.OOXMLFile(...).encrypt`)
from documents written by python-docx, openpyxl and python-pptx. The tool is
not a dependency of PipesHub; it was run once to produce these files.

The macro-enabled files are the same documents with the main part's content type in `[Content_Types].xml` changed to the
`macroEnabled` one before encryption (no macros inside).
