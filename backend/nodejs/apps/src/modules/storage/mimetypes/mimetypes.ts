export const extensionToMimeType: Record<string, string> = {
  // Google Workspace formats
  gdoc: 'application/vnd.google-apps.document',
  gsheet: 'application/vnd.google-apps.spreadsheet',
  gslides: 'application/vnd.google-apps.presentation',
  gdraw: 'application/vnd.google-apps.drawing',
  gform: 'application/vnd.google-apps.form',
  gmap: 'application/vnd.google-apps.map',
  gsite: 'application/vnd.google-apps.site',
  gscript: 'application/vnd.google-apps.script',

  // Image formats
  jp2: 'image/jp2',
  jpx: 'image/jpx',
  jpm: 'image/jpm',
  jxl: 'image/jxl',
  jxs: 'image/jxs',
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
  psd: 'image/vnd.adobe.photoshop',
  heic: 'image/heic',
  heif: 'image/heif',
  avif: 'image/avif',
  jxr: 'image/jxr',
  icns: 'image/x-icns',
  

  // Document formats
  pdf: 'application/pdf',
  txt: 'text/plain',
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  doc: 'application/msword',
  xls: 'application/vnd.ms-excel',
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  ppt: 'application/vnd.ms-powerpoint',
  pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  odt: 'application/vnd.oasis.opendocument.text',
  ods: 'application/vnd.oasis.opendocument.spreadsheet',
  odp: 'application/vnd.oasis.opendocument.presentation',
  rtf: 'text/rtf',
  epub: 'application/epub+zip',

  // Web formats
  html: 'text/html',
  htm: 'text/html',
  css: 'text/css',
  js: 'application/javascript',
  json: 'application/json',
  xml: 'text/xml',
  csv: 'text/csv',
  tsv: 'text/tab-separated-values',
  md: 'text/markdown',
  mdx: 'text/mdx',
  appmdx: 'application/mdx',

  // Archive and compression formats
  zip: 'application/zip',
  zst: 'application/zstd',
  cab: 'application/vnd.ms-cab-compressed',
  rpm: 'application/x-rpm',
  deb: 'application/vnd.debian.binary-package',
  cpio: 'application/x-cpio',
  lz: 'application/lzip',
  '7z': 'application/x-7z-compressed',
  rar: 'application/x-rar-compressed',
  tar: 'application/x-tar',
  gz: 'application/gzip',
  bz2: 'application/x-bzip2',
  xz: 'application/x-xz',

  // Audio formats
  ape: 'audio/ape',
  amr: 'audio/amr',
  au: 'audio/basic',
  qcp: 'audio/qcelp',
  mp3: 'audio/mpeg',
  wav: 'audio/wav',
  ogg: 'application/ogg',
  flac: 'audio/flac',
  aac: 'audio/aac',
  m4a: 'audio/x-m4a',
  midi: 'audio/midi',
  mid: 'audio/midi',

  // Video formats
  mp4: 'video/mp4',
  avi: 'video/x-msvideo',
  mkv: 'video/x-matroska',
  mov: 'video/quicktime',
  wmv: 'video/x-ms-wmv',
  flv: 'video/x-flv',
  '3gp': 'video/3gpp',
  webm: 'video/webm',

  // Font formats
  ttf: 'font/ttf',
  otf: 'font/otf',
  woff: 'font/woff',
  woff2: 'font/woff2',
  eot: 'application/vnd.ms-fontobject',

  // Microsoft Office legacy formats
  xlt: 'application/vnd.ms-excel',
  xlm: 'application/vnd.ms-excel',
  pps: 'application/vnd.ms-powerpoint',
  pot: 'application/vnd.ms-powerpoint',

  // OpenDocument formats
  ott: 'application/vnd.oasis.opendocument.text-template',
  ots: 'application/vnd.oasis.opendocument.spreadsheet-template',
  otp: 'application/vnd.oasis.opendocument.presentation-template',
  odg: 'application/vnd.oasis.opendocument.graphics',
  odf: 'application/vnd.oasis.opendocument.formula',

  // Programming and data formats
  py: 'text/x-python',
  java: 'text/x-java-source',
  c: 'text/x-c',
  cpp: 'text/x-c++',
  php: 'text/x-php',
  sql: 'application/sql',
  yaml: 'application/x-yaml',
  yml: 'application/x-yaml',

  // Database formats
  mdb: 'application/x-msaccess',
  sqlite: 'application/vnd.sqlite3',
  db: 'application/octet-stream',

  // Special formats
  exe: 'application/vnd.microsoft.portable-executable',
  dll: 'application/x-msdownload',
  so: 'application/x-sharedlib',
  dylib: 'application/x-mach-o-dylib',

  // WOPI test formats (specific to your existing implementation)
  wopitest:
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  wopitestx:
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',

  // CAD and 3D formats
  dwg: 'image/vnd.dwg',
  dxf: 'image/vnd.dxf',
  x3d: 'model/x3d+xml',
  glb: 'model/gltf-binary',
  gltf: 'model/gltf+json',
  stl: 'model/stl',
  '3mf': 'application/vnd.ms-package.3dmanufacturing-3dmodel+xml',

  // GIS and mapping formats
  kml: 'application/vnd.google-earth.kml+xml',
  kmz: 'application/vnd.google-earth.kmz',
  gpx: 'application/gpx+xml',
  shp: 'application/vnd.shp',
  shx: 'application/vnd.shx',
  dbf: 'application/x-dbf',
  geojson: 'application/geo+json',

  // Scientific and medical formats
  fits: 'application/fits',
  dcm: 'application/dicom',
  nii: 'application/x-nifti',
  mrc: 'application/marc',

  // Other common formats
  bin: 'application/octet-stream',
  dat: 'application/octet-stream',
  log: 'text/plain',
  msg: 'application/vnd.ms-outlook',
  pages: 'application/x-iwork-pages-sffpages',
  numbers: 'application/x-iwork-numbers-sffnumbers',
  key: 'application/x-iwork-keynote-sffkey',
};

// Helper function to get mimetype
export const getMimeType = (extension: string): string => {
  if (!extension) {
    return '';
  }
  const normalizedExtension = extension.toLowerCase().replace('.', '');
  return extensionToMimeType[normalizedExtension] || '';
};
