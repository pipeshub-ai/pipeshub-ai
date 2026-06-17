export const extensionToMimeType: Record<string, string> = {
  // Google Workspace formats (used by connectors)
  gdoc: 'application/vnd.google-apps.document',
  gsheet: 'application/vnd.google-apps.spreadsheet',
  gslides: 'application/vnd.google-apps.presentation',

  // Image formats
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  png: 'image/png',
  PNG: 'image/png',
  JPEG: 'image/jpeg',
  JPG: 'image/jpeg',
  gif: 'image/gif',
  GIF: 'image/gif',
  svg: 'image/svg+xml',
  webp: 'image/webp',
  bmp: 'image/bmp',
  ico: 'image/x-icon',
  tiff: 'image/tiff',
  heic: 'image/heic',
  heif: 'image/heif',
  avif: 'image/avif',
  

  // Document formats
  pdf: 'application/pdf',
  txt: 'text/plain',
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  doc: 'application/msword',
  xls: 'application/vnd.ms-excel',
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  ppt: 'application/vnd.ms-powerpoint',
  pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',

  // Web and structured text formats
  html: 'text/html',
  htm: 'text/html',
  csv: 'text/csv',
  tsv: 'text/tab-separated-values',
  md: 'text/markdown',
  mdx: 'text/mdx',

  // WOPI test formats (specific to existing implementation)
  wopitest:
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  wopitestx:
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
};

// Helper function to get mimetype
export const getMimeType = (extension: string): string => {
  if (!extension) {
    return '';
  }
  const normalizedExtension = extension.toLowerCase().replace('.', '');
  return extensionToMimeType[normalizedExtension] || '';
};
