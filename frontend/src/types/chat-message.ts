import type { CustomCitation, FormattedMessage } from './chat-bot';

export interface ChatMessageProps {
  message: FormattedMessage;
  index: number;
  onRegenerate: (messageId: string) => Promise<void>;
  onFeedbackSubmit: (messageId: string, feedback: any) => Promise<void>;
  conversationId: string | null;
  onViewPdf: (url: string,citation : CustomCitation, citations: CustomCitation[], isExcelFile?: boolean,buffer?: ArrayBuffer) => void;
  isRegenerating: boolean;
  showRegenerate: boolean;
}

export interface StyledCitationProps {
  children: React.ReactNode;
  onMouseEnter: () => void;
  onMouseLeave: () => void;
}

export interface MessageContentProps {
  content: string;
  citations: CustomCitation[];
  onRecordClick: (record: Record) => void;
  aggregatedCitations: { [key: string]: CustomCitation[] };
  onViewPdf: (url: string,citation : CustomCitation, citations: CustomCitation[], isExcelFile?: boolean,buffer?: ArrayBuffer) => Promise<void>;
}

export interface Record {
  recordId: string;
  citations?: CustomCitation[];
  [key: string]: any;
}
