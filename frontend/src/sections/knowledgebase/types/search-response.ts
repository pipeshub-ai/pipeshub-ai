export interface BoundingBox {
  x: number;
  y: number;
}

export interface Department {
  _id: string;
  name: string;
}

export interface AppSpecificRecordType {
  _id: string;
  name: string;
  tag: string;
}

export interface DocumentMetadata {
  recordId: string;
  recordName: string;
  orgId: string;
  blockNum: number[];
  blockText: string;
  pageNum: number[];
  subcategoryLevel1: string;
  subcategoryLevel2: string;
  subcategoryLevel3: string;
  categories: string;
  departments: string[];
  connector: string;
  recordType: string;
  blockType: number;
  mimeType: string;
  recordVersion: number;
  topics: string[];
  languages: string[];
  bounding_box: BoundingBox[];
  origin: string;
  extension: string;
  rowNum?: number;
  sheetNum?: number;
  sheetName?: string;
  _id: string;
  _collection_name: string;
}

export interface DocumentContent {
  content: string;
  score?: number;
  citationType: string;
  metadata: DocumentMetadata;
  chunkIndex?: number;
  citationId?: string;
  id?: string;
}

export interface AggregatedDocument {
  recordId: string;
  documents: DocumentContent[];
}

export namespace PipesHub {
  export interface Record {
    _key: string;
    _id: string;
    _rev: string;
    orgId: string;
    recordName: string;
    externalRecordId: string;
    recordType: string;
    origin: string;
    createdAtTimestamp: number;
    updatedAtTimestamp: number;
    isDeleted: boolean;
    isArchived: boolean;
    indexingStatus: string;
    version: number;
    extractionStatus: string;
    name: string;
    isFile: boolean;
    extension: string;
    mimeType: string;
    sizeInBytes: number;
    webUrl: string;
    path: string;
    [key: string]: any;
  }
}

export interface SearchResult {
  content: string;
  score: number;
  citationType: string;
  metadata: DocumentMetadata;
  citationId?: string;
  id?: string;
}

export interface SearchResponse {
  searchId: string;
  searchResponse: {
    searchResults: SearchResult[];
    records: PipesHub.Record[];
  };
}

export interface SearchFilters {
  department?: string[];
  moduleId?: string[];
  appSpecificRecordType?: string[];
  app?: string[];
  kb?:string[];
}

export interface KnowledgeSearchProps {
  searchResults: SearchResult[];
  loading: boolean;
  canLoadMore: boolean;
  onSearchQueryChange: (query: string) => void;
  onTopKChange: (callback: (prevTopK: number) => number) => void;
  onViewCitations: (recordId: string, extension: string, recordCitation?: SearchResult) => Promise<void>;
  recordsMap: Record<string, PipesHub.Record>;
  allConnectors: any[];
}
