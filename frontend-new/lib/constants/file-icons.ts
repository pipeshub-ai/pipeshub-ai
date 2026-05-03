// File icon styles available for each file type
export const FILE_ICON_STYLES = ['color', 'flat', 'line'] as const;
export type FileIconStyle = typeof FILE_ICON_STYLES[number];

// Comprehensive file icon map with metadata
export const FILE_ICON_MAP = {
  // Documents
  pdf: { name: 'PDF', category: 'document' },
  doc: { name: 'Word Document', category: 'document' },
  txt: { name: 'Text File', category: 'document' },

  // Spreadsheets
  csv: { name: 'CSV', category: 'spreadsheet' },
  xls: { name: 'Excel', category: 'spreadsheet' },

  // Presentations
  ppt: { name: 'PowerPoint', category: 'presentation' },

  // Web
  html: { name: 'HTML', category: 'web' },
  css: { name: 'CSS', category: 'web' },
  js: { name: 'JavaScript', category: 'web' },
  json: { name: 'JSON', category: 'web' },

  // Images
  png: { name: 'PNG', category: 'image' },
  jpg: { name: 'JPG', category: 'image' },
  jpeg: { name: 'JPEG', category: 'image' },
  gif: { name: 'GIF', category: 'image' },
  webp: { name: 'WebP', category: 'image' },
  ico: { name: 'Icon', category: 'image' },
  tiff: { name: 'TIFF', category: 'image' },
  svg: { name: 'SVG', category: 'image' },

  // Video
  mp4: { name: 'MP4', category: 'video' },
  mov: { name: 'MOV', category: 'video' },
  mpg: { name: 'MPG', category: 'video' },
  avi: { name: 'AVI', category: 'video' },

  // Audio
  mp3: { name: 'MP3', category: 'audio' },
  wav: { name: 'WAV', category: 'audio' },

  // Archive
  zip: { name: 'ZIP', category: 'archive' },
  rar: { name: 'RAR', category: 'archive' },

  // Design
  psd: { name: 'Photoshop', category: 'design' },
  ai: { name: 'Illustrator', category: 'design' },
  fig: { name: 'Figma', category: 'design' },
  blend: { name: 'Blender', category: 'design' },
  cdr: { name: 'CorelDRAW', category: 'design' },
  skt: { name: 'Sketch', category: 'design' },
  c4d: { name: 'Cinema 4D', category: 'design' },
  aep: { name: 'After Effects', category: 'design' },

  // Code/System
  java: { name: 'Java', category: 'code' },
  exe: { name: 'Executable', category: 'system' },
  dmg: { name: 'DMG', category: 'system' },
} as const;

export type FileIconExtension = keyof typeof FILE_ICON_MAP;
