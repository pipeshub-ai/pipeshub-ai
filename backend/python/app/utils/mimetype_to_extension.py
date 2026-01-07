mime_to_extension = {
    # PNG
    "image/png": "png",
    "image/x-png": "png",
    "image/png; charset=binary": "png",
    "application/png": "png",
    "application/x-png": "png",
    "image/vnd.mozilla.apng": "png",
    
    # JPEG/JPG
    "image/jpeg": "jpeg",
    "image/jpg": "jpg",
    "image/x-jpeg": "jpeg",
    "image/x-jpg": "jpg",
    "image/pjpeg": "jpeg",
    "image/jpeg; charset=binary": "jpeg",
    "image/jpg; charset=binary": "jpg",
    
    # WEBP
    "image/webp": "webp",
    "image/x-webp": "webp",
    
    # SVG
    "image/svg+xml": "svg",
    "image/svg": "svg",
    "image/svg+xml; charset=utf-8": "svg",
    "application/svg+xml": "svg",
    "text/xml-svg": "svg",
    "application/xml-svg": "svg",
    
    # PDF
    "application/pdf": "pdf",
    "application/x-pdf": "pdf",
    "application/acrobat": "pdf",
    "application/vnd.pdf": "pdf",
    "text/pdf": "pdf",
    "text/x-pdf": "pdf",
    "application/force-download": "pdf",
    
    # DOCX
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document+xml": "docx",
    
    # DOC
    "application/msword": "doc",
    "application/x-msword": "doc",
    "application/msword; charset=utf-8": "doc",
    "application/x-msword; charset=utf-8": "doc",
    "application/doc": "doc",
    "application/x-doc": "doc",
    "zz-application/zz-winassoc-doc": "doc",
    
    # XLSX
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet+xml": "xlsx",
    
    # XLS
    "application/vnd.ms-excel": "xls",
    "application/vnd.ms-excel.sheet.macroEnabled.12": "xls",
    "application/x-msexcel": "xls",
    "application/x-excel": "xls",
    "application/excel": "xls",
    "application/xls": "xls",
    "application/x-xls": "xls",
    "application/vnd.ms-excel; charset=utf-8": "xls",
    "zz-application/zz-winassoc-xls": "xls",
    
    # CSV
    "text/csv": "csv",
    "application/csv": "csv",
    "text/comma-separated-values": "csv",
    "text/x-comma-separated-values": "csv",
    "text/x-csv": "csv",
    "application/csv; charset=utf-8": "csv",
    "text/csv; charset=utf-8": "csv",
    "text/csv; charset=us-ascii": "csv",
    
    # PPTX
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation+xml": "pptx",
    
    # PPT
    "application/vnd.ms-powerpoint": "ppt",
    "application/vnd.ms-powerpoint.presentation.macroEnabled.12": "ppt",
    "application/x-mspowerpoint": "ppt",
    "application/powerpoint": "ppt",
    "application/x-ppt": "ppt",
    "application/vnd.ms-powerpoint; charset=utf-8": "ppt",
    "zz-application/zz-winassoc-ppt": "ppt",
    
    # MDX
    "text/mdx": "mdx",
    "text/x-mdx": "mdx",
    "application/mdx": "mdx",
    "application/x-mdx": "mdx",
    "text/mdx; charset=utf-8": "mdx",
    
    "text/plain": "txt",
    "text/plain; charset=utf-8": "txt",
    "text/plain; charset=us-ascii": "txt",
    "text/plain; charset=iso-8859-1": "txt",
    "text/plain; charset=windows-1252": "txt",
    "text/plain; charset=ascii": "txt",
    "text/x-text": "txt",
    "text/txt": "txt",
    "application/text": "txt",
    "application/txt": "txt",
    "text/html": "html",
    "text/html; charset=utf-8": "html",
    "text/html; charset=us-ascii": "html",
    "text/html; charset=iso-8859-1": "html",
    "text/html; charset=windows-1252": "html",
    "text/html; charset=ascii": "html",
    "application/xhtml+xml": "html",
    "application/xhtml": "html",
    "text/xhtml": "html",
    "application/html": "html",
    "text/markdown": "md",
    "text/x-markdown": "md",
    "text/x-md": "md",
    "application/markdown": "md",
    "application/x-markdown": "md",
    "text/markdown; charset=utf-8": "md",
    "text/markdown; charset=us-ascii": "md",
    "text/gmail_content": "html",
}


def get_extension_from_mimetype(mime_type) -> str | None:
    return mime_to_extension.get(mime_type)
