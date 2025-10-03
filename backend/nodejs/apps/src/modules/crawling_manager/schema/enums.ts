// Enums for the Crawling Manager

export enum CrawlingScheduleType {
    HOURLY = 'hourly',
    DAILY = 'daily',
    WEEKLY = 'weekly',
    MONTHLY = 'monthly',
    CUSTOM = 'custom',
    ONCE = 'once',
}

export enum CrawlingStatus {
    IDLE = 'idle',
    RUNNING = 'running',
    PAUSED = 'paused',
    STOPPED = 'stopped',
    ERROR = 'error',
    COMPLETED = 'completed',
    FAILED = 'failed',
}

export enum FileFormatType {
    DOCUMENT = 'document',
    SPREADSHEET = 'spreadsheet',
    PRESENTATION = 'presentation',
    PDF = 'pdf',
    IMAGE = 'image',
    AUDIO = 'audio',
    VIDEO = 'video',
    CODE = 'code',
    ARCHIVE = 'archive',
    OTHER = 'other'
}

export enum CrawlingType {
    FULL = 'full',
    INCREMENTAL = 'incremental',
    DIFF = 'diff',
}

export const FILE_FORMAT_EXTENSIONS = {
    [FileFormatType.DOCUMENT]: [
      'doc', 'docx', 'txt', 'rtf', 'odt', 'ods', 'odp', 'epub', 'html', 'htm', 
      'md', 'mdx', 'appmdx', 'xlt', 'xlm', 'ott', 'ots', 'otp', 'odg', 'odf',
      'pages', 'numbers', 'key', 'msg', 'gdoc', 'gsheet', 'gslides', 'gdraw', 
      'gform', 'gmap', 'gsite', 'gscript'
    ],
    [FileFormatType.SPREADSHEET]: ['xls', 'xlsx', 'csv', 'tsv', 'ods', 'gsheet', 'xlt', 'xlm'],
    [FileFormatType.PRESENTATION]: ['ppt', 'pptx', 'pps', 'pot', 'odp', 'otp', 'gslides', 'key'],
    [FileFormatType.PDF]: ['pdf'],
    [FileFormatType.IMAGE]: [
      'jpg', 'jpeg', 'png', 'gif', 'bmp', 'svg', 'webp', 'tiff', 'jp2', 'jpx', 
      'jpm', 'jxl', 'jxs', 'ico', 'psd', 'heic', 'heif', 'avif', 'jxr', 'icns'
    ],
    [FileFormatType.AUDIO]: [
      'mp3', 'wav', 'ogg', 'flac', 'aac', 'm4a', 'ape', 'amr', 'au', 'qcp', 'midi', 'mid'
    ],
    [FileFormatType.VIDEO]: ['mp4', 'avi', 'mkv', 'mov', 'wmv', 'flv', '3gp', 'webm'],
    [FileFormatType.CODE]: [
      'js', 'json', 'css', 'xml', 'py', 'java', 'c', 'cpp', 'php', 'sql', 'yaml', 'yml'
    ],
    [FileFormatType.ARCHIVE]: [
      'zip', 'rar', 'tar', 'gz', '7z', 'zst', 'cab', 'rpm', 'deb', 'cpio', 'lz', 'bz2', 'xz'
    ],
    [FileFormatType.OTHER]: [
      'ttf', 'otf', 'woff', 'woff2', 'eot', 'mdb', 'sqlite', 'db', 'exe', 'dll', 
      'so', 'dylib', 'wopitest', 'wopitestx', 'dwg', 'dxf', 'x3d', 'glb', 'gltf', 
      'stl', '3mf', 'kml', 'kmz', 'gpx', 'shp', 'shx', 'dbf', 'geojson', 'fits', 
      'dcm', 'nii', 'mrc', 'bin', 'dat', 'log', '*'
    ]
  } as const;