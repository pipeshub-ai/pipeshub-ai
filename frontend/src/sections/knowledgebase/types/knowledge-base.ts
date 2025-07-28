import type { Theme, SxProps } from '@mui/material';
import type { Icon as IconifyIcon } from '@iconify/react';

export interface Department {
  _id: string;
  name: string;
}

export interface AppSpecificRecordType {
  _id: string;
  name: string;
}

export interface FileRecord {
  name: string;
  extension: string;
  mimeType: string;
  sizeInBytes: number;
  isFile: boolean;
  webUrl: string;
}

export interface Record {
  id: string;
  externalRecordId: string;
  recordName: string;
  recordType: 'FILE' | 'CONNECTOR' | 'LINK' | 'FAQ' | 'MAIL';
  origin: 'UPLOAD' | 'CONNECTOR';
  indexingStatus: string | null;
  createdAtTimestamp: number;
  updatedAtTimestamp: number;
  sourceCreatedAtTimestamp: number;
  sourceLastModifiedTimestamp: number;
  orgId: string;
  version: number | null;
  isDeleted: boolean;
  deletedByUserId: string | null;
  isLatestVersion: boolean | null;
  fileRecord: FileRecord;
}

export interface Pagination {
  page: number;
  limit: number;
  totalCount: number;
  totalPages: number;
}

export interface Filters {
  recordTypes?: string[];
  origin?: string[];
  indexingStatus?: string[];
  department?: string[];
  moduleId?: string[];
  searchTags?: string[];
  appSpecificRecordType?: string[];
  status?: string[];
  connectors?: string[];
  app?: string[];
  permissions?: string[];
  kb?:string[];
}

export interface FilterHeaderProps {
  expanded: boolean;
  onClick: () => void;
  children?: React.ReactNode;
  theme?: Theme;
}

export interface FilterSectionComponentProps {
  id: string;
  icon: React.ComponentProps<typeof IconifyIcon>['icon'];
  label: string;
  filterType: keyof Filters;
  items: any[]; // This should be more specific based on your data structure
  getItemId?: (item: any) => string;
  getItemLabel?: (item: any) => string;
  renderItemLabel?: ((item: any) => React.ReactNode) | null;
}

export interface FilterOption {
  value: string | null;
  count: number;
}

export interface KnowledgeBaseResponse {
  records: Record[];
  pagination: Pagination;
  filters: {
    applied: any;
    available: {
      recordTypes: FilterOption[];
      origins: FilterOption[];
      indexingStatus: FilterOption[];
      [key: string]: any;
    };
  };
  meta: {
    requestId: string;
    timestamp: string;
  };
}

export interface KnowledgeBaseDetailsProps {
  knowledgeBaseData: KnowledgeBaseResponse | null;
  onSearchChange: (query: string) => void;
  loading: boolean;
  pagination: {
    page: number;
    limit: number;
  };
  onPageChange: (page: number) => void;
  onLimitChange: (limit: number) => void;
}

export interface KnowledgeBaseSideBarProps {
  filters: Filters;
  onFilterChange: (filters: Filters) => void;
  openSidebar: boolean;
  onToggleSidebar: () => void;
}

export interface KnowledgeSearchSideBarProps {
  filters: Filters;
  onFilterChange: (filters: Filters) => void;
  sx: SxProps<Theme>;
  openSidebar: boolean;
  onToggleSidebar: () => void;
}
