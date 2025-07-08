export interface ApiCitation {
  citationId: string;
  citationData?: {
    _id: string;
    content: string;
    subcategoryLevel1: string;
    subcategoryLevel2: string;
    subcategoryLevel3: string;
    categories: string;
    departments: string[];
    connector: string;
    recordType: string;
    orgId: string;
    blockType: number;
    mimeType: string;
    recordId: string;
    recordVersion: number;
    topics: string[];

    documentIndex: number;
    citationMetaData: {
      rowNum?: any;
      recordId?: string;
      [key: string]: any;
    };
    citationType: string;
    relatedCitations: any[];
    isDeleted: boolean;
    usageCount: number;
    verificationStatus: string;
    createdAt: string;
    updatedAt: string;
    slug: string;
  };
  orgId: string;
  citationType: string;
  [key: string]: any;
}

export interface BoundingBox {
  x: number;
  y: number;
  _id: string;
}

export interface Metadata {
  _id: string;
  blockNum: number[];
  pageNum: number[];
  blockText: string;
  subcategoryLevel1: string;
  subcategoryLevel2: string;
  subcategoryLevel3: string;
  categories: string;
  departments: string[];
  connector: string;
  recordType: string;
  orgId: string;
  blockType: number;
  mimeType: string;
  recordId: string;
  recordVersion: number;
  topics: string[];
  languages: string[];
  bounding_box: BoundingBox[];
  recordName: string;
  origin: string;
  extension: string;
  rowNum?: number;
  sheetNum?:number;
  sheetName?:string;
  _collection_name: string;
  webUrl?:string;
}

// The Citation interface as specified
export interface Citation {
  citationId: string;
  citationData: {
    _id: string;
    content: string;
    chunkIndex: number;
    metadata: Metadata;
    citationType: string;
    createdAt: string;
    updatedAt: string;
  };
  [key: string]: any;
}

export interface Message {
  _id: string;
  messageType: string;
  content: string;
  contentFormat: string;
  citations: Citation[];
  followUpQuestions: string[];
  feedback: any[];
  createdAt: string;
  updatedAt: string;
  confidence?: string;
}

export interface ConversationFilters {
  applied: {
    filters: any[];
    values: Record<string, any>;
  };
  available: {
    messageType: {
      values: string[];
      description: string;
      current: null;
    };
    dateRange: {
      type: string;
      description: string;
      format: string;
      current: {
        startDate: null;
        endDate: null;
      };
    };
    sorting: {
      sortBy: {
        values: string[];
        default: string;
        description: string;
        current: string;
      };
      sortOrder: {
        values: string[];
        default: string;
        description: string;
        current: string;
      };
    };
    pagination: {
      description: string;
      currentPage: number;
      itemsPerPage: number;
      orderDirection: string;
    };
  };
}

export interface ConversationPagination {
  page: number;
  limit: number;
  totalCount: number;
  totalPages: number;
  hasNextPage: boolean;
  hasPrevPage: boolean;
  messageRange: {
    start: number;
    end: number;
  };
}

export interface ConversationAccess {
  isOwner: boolean;
  accessLevel: string;
}

export interface Conversation {
  _id: string;
  userId: string;
  orgId: string;
  title: string;
  initiator: string;
  messages: Message[];
  isShared: boolean;
  isDeleted: boolean;
  isArchived: boolean;
  lastActivityAt: string;
  sharedWith: string[];
  createdAt: string;
  updatedAt: string;
  __v: number;
  pagination?: ConversationPagination;
  access?: ConversationAccess;
  filters?: ConversationFilters;
  status?: string;
  failReason?: string;
}

export interface ApiResponse {
  conversation: Conversation;
  meta: {
    requestId: string;
    timestamp: string;
    duration: number;
    conversationId?: string;
    messageCount?: number;
  };
}

export interface CustomCitation {
  id: string;
  _id: string;
  citationId: string;
  content: string;
  metadata: Metadata;
  orgId: string;
  citationType: string;
  createdAt: string;
  updatedAt: string;
  chunkIndex: number;
}

export interface FormattedMessage {
  id: string;
  timestamp: Date;
  content: string;
  type: string;
  contentFormat: string;
  followUpQuestions: string[];
  createdAt: Date;
  updatedAt: Date;
  feedback?: any[];
  confidence?: string;
  citations?: CustomCitation[];
  messageType?: string;
  error?: boolean;
  [key: string]: any;
}

export interface ChatHeaderProps {
  isDrawerOpen: boolean;
  onDrawerOpen: () => void;
  conversationId: string | null;
}

export interface ChatProps {
  onClose: () => void;
  onChatSelect: (chat: Conversation) => void;
  onNewChat: () => void;
  selectedId: string | null;
  shouldRefresh: boolean;
  onRefreshComplete: () => void;
}

export interface ExpandedCitationsState {
  [key: number]: boolean;
}

export interface CompletionData {
  conversation?: Conversation;
  recordsUsed?: number;
  meta?: {
    requestId: string;
    timestamp: string;
    duration: number;
    recordsUsed: number;
  };
  status?: string;
  error?: string;
}
