// Common types used across the application

// File types supported by the system
export type FileType =
  | 'PDF'
  | 'DOC'
  | 'DOCX'
  | 'TXT'
  | 'XLS'
  | 'XLSX'
  | 'PPT'
  | 'PPTX'
  | 'CSV'
  | 'JSON'
  | 'MD';

// Processing/indexing status for items
export type ItemStatus = 'indexed' | 'pending' | 'processing' | 'failed';

// Source of content
export type SourceType = 'knowledge_base' | 'gdrive' | 'jira' | 'slack';

// Common sorting
export type SortOrder = 'asc' | 'desc';

// Common view modes
export type ViewMode = 'list' | 'grid';
