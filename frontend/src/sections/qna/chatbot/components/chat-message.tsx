import type { Metadata, CustomCitation } from 'src/types/chat-bot';
import type { Record, ChatMessageProps } from 'src/types/chat-message';

import remarkGfm from 'remark-gfm';
import { Icon } from '@iconify/react';
import ReactMarkdown from 'react-markdown';
import upIcon from '@iconify-icons/mdi/chevron-up';
import eyeIcon from '@iconify-icons/mdi/eye-outline';
import refreshIcon from '@iconify-icons/mdi/refresh';
import loadingIcon from '@iconify-icons/mdi/loading';
import downIcon from '@iconify-icons/mdi/chevron-down';
import robotIcon from '@iconify-icons/mdi/robot-outline';
import rightIcon from '@iconify-icons/mdi/chevron-right';
import accountIcon from '@iconify-icons/mdi/account-outline';
import fileDocIcon from '@iconify-icons/mdi/file-document-outline';
import React, {
  useRef,
  useMemo,
  useState,
  useCallback,
  Fragment,
  useContext,
  createContext,
  useEffect,
} from 'react';

import {
  Box,
  Chip,
  Fade,
  Paper,
  Stack,
  Dialog,
  Button,
  Popper,
  Tooltip,
  Divider,
  Collapse,
  Typography,
  IconButton,
  DialogTitle,
  DialogContent,
  CircularProgress,
  ClickAwayListener,
  alpha,
  useTheme,
} from '@mui/material';

import RecordDetails from './record-details';
import MessageFeedback from './message-feedback';
import CitationHoverCard from './citations-hover-card';
import { extractAndProcessCitations } from '../utils/styles/content-processing';

interface StreamingContextType {
  streamingState: {
    messageId: string | null;
    content: string;
    citations: CustomCitation[];
    isActive: boolean;
  };
  updateStreamingContent: (messageId: string, content: string, citations: CustomCitation[]) => void;
  clearStreaming: () => void;
}

export const StreamingContext = createContext<StreamingContextType | null>(null);

export const useStreamingContent = () => {
  const context = useContext(StreamingContext);
  if (!context) {
    throw new Error('useStreamingContent must be used within StreamingProvider');
  }
  return context;
};

// // Content processing utilities
// const processMarkdownContent = (content: string): string => {
//   if (!content) return '';

//   return (
//     content
//       // Fix escaped newlines
//       .replace(/\\n/g, '\n')
//       // Fix citation formatting - convert **number** to [number]
//       // .replace(/\*\*(\d+)\*\*/g, '[$1]')
//       // // Preserve other bold formatting
//       // .replace(/\*\*([^*]+)\*\*/g, '**$1**')
//       // // Clean up multiple newlines (but preserve intentional spacing)
//       // .replace(/\n{4,}/g, '\n\n\n')
//       // // Fix list spacing issues
//       // .replace(/(\n\d+\.\s)/g, '\n$1')
//       // .replace(/(\n[-*]\s)/g, '\n$1')
//       // // Clean up code block formatting
//       // .replace(/```\n\n+```/g, '```\n```')
//       // // Ensure proper spacing around code blocks
//       // .replace(/([^\n])```/g, '$1\n```')
//       // .replace(/```([^\n])/g, '```\n$1')
//       // Clean up trailing whitespace but preserve structure
//       .trim()
//   );
// };

// const extractAndProcessCitations = (
//   content: string,
//   streamingCitations: CustomCitation[] = []
// ): {
//   processedContent: string;
//   citations: CustomCitation[];
//   citationMap: { [key: number]: CustomCitation };
// } => {
//   // Extract citation numbers from content
//   const citationMatches = Array.from(content.matchAll(/\[(\d+)\]/g));
//   const citationNumbers = new Set(citationMatches.map((match) => parseInt(match[1], 10)));

//   // Build citation map - prefer streaming citations, fall back to content-based numbering
//   const citationMap: { [key: number]: CustomCitation } = {};
//   const processedCitations: CustomCitation[] = [];

//   // First, map citations by their chunkIndex if available
//   streamingCitations.forEach((citation, index) => {
//     const citationNumber = citation.chunkIndex || index + 1;
//     if (!citationMap[citationNumber]) {
//       citationMap[citationNumber] = citation;
//       processedCitations.push(citation);
//     }
//   });

//   // Ensure we have citations for all numbers mentioned in content
//   citationNumbers.forEach((num) => {
//     if (!citationMap[num] && streamingCitations[num - 1]) {
//       citationMap[num] = streamingCitations[num - 1];
//       if (!processedCitations.includes(streamingCitations[num - 1])) {
//         processedCitations.push(streamingCitations[num - 1]);
//       }
//     }
//   });

//   // Process the content for better markdown rendering
//   const processedContent = processMarkdownContent(content);

//   return {
//     processedContent,
//     citations: processedCitations,
//     citationMap,
//   };
// };

const formatTime = (createdAt: Date) => {
  const date = new Date(createdAt);
  return new Intl.DateTimeFormat('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: true,
  }).format(date);
};

const formatDate = (createdAt: Date) => {
  const date = new Date(createdAt);
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);

  if (date.toDateString() === today.toDateString()) {
    return 'Today';
  }
  if (date.toDateString() === yesterday.toDateString()) {
    return 'Yesterday';
  }
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
  }).format(date);
};

function isDocViewable(extension: string) {
  const viewableExtensions = [
    'pdf',
    'xlsx',
    'xls',
    'csv',
    'docx',
    'html',
    'txt',
    'md',
    'ppt',
    'pptx',
  ];
  return viewableExtensions.includes(extension);
}

// StreamingContent component with proper processing
const StreamingContent = React.memo(
  ({
    messageId,
    fallbackContent,
    fallbackCitations,
    onRecordClick,
    aggregatedCitations,
    onViewPdf,
  }: {
    messageId: string;
    fallbackContent: string;
    fallbackCitations: CustomCitation[];
    onRecordClick: (record: Record) => void;
    aggregatedCitations: { [key: string]: CustomCitation[] };
    onViewPdf: (
      url: string,
      citation: CustomCitation,
      citations: CustomCitation[],
      isExcelFile?: boolean,
      buffer?: ArrayBuffer
    ) => Promise<void>;
  }) => {
    const { streamingState } = useStreamingContent();
    const [hoveredCitationId, setHoveredCitationId] = useState<string | null>(null);
    const [hoveredRecordCitations, setHoveredRecordCitations] = useState<CustomCitation[]>([]);
    const hoverTimeoutRef = useRef<NodeJS.Timeout | null>(null);
    const [hoveredCitation, setHoveredCitation] = useState<CustomCitation | null>(null);
    const [popperAnchor, setPopperAnchor] = useState<null | {
      getBoundingClientRect: () => DOMRect;
    }>(null);

    // Determine if this message is currently streaming
    const isStreaming = streamingState.messageId === messageId && streamingState.isActive;

    // Process content and citations properly
    const {
      processedContent,
      citations: processedCitations,
      citationMap,
    } = useMemo(() => {
      const rawContent =
        isStreaming && streamingState.content ? streamingState.content : fallbackContent;

      const rawCitations =
        isStreaming && streamingState.citations?.length > 0
          ? streamingState.citations
          : fallbackCitations;

      return extractAndProcessCitations(rawContent, rawCitations);
    }, [
      isStreaming,
      streamingState.content,
      streamingState.citations,
      fallbackContent,
      fallbackCitations,
    ]);

    // Show streaming indicator when actively streaming
    const showStreamingIndicator = isStreaming && processedContent.length > 0;

    const handleMouseEnter = useCallback(
      (event: React.MouseEvent, citationRef: string, citationId: string) => {
        if (hoverTimeoutRef.current) clearTimeout(hoverTimeoutRef.current);

        setPopperAnchor({
          getBoundingClientRect: () => ({
            width: 0,
            height: 0,
            top: event.clientY,
            right: event.clientX,
            bottom: event.clientY,
            left: event.clientX,
            x: event.clientX,
            y: event.clientY,
            toJSON: () => '',
          }),
        });

        const citationNumber = parseInt(citationRef.replace(/[[\]]/g, ''), 10);
        const citation = citationMap[citationNumber];

        if (citation) {
          if (citation.metadata?.recordId) {
            const recordCitations = aggregatedCitations[citation.metadata.recordId] || [];
            setHoveredRecordCitations(recordCitations);
          }
          setHoveredCitation(citation);
          setHoveredCitationId(citationId);
        }
      },
      [citationMap, aggregatedCitations]
    );

    const handleCloseHoverCard = useCallback(() => {
      setHoveredCitationId(null);
      setHoveredRecordCitations([]);
      setHoveredCitation(null);
      setPopperAnchor(null);
    }, []);

    const handleMouseLeave = useCallback(() => {
      hoverTimeoutRef.current = setTimeout(() => {
        handleCloseHoverCard();
      }, 300);
    }, [handleCloseHoverCard]);

    const handleHoverCardMouseEnter = useCallback(() => {
      if (hoverTimeoutRef.current) clearTimeout(hoverTimeoutRef.current);
    }, []);

    const handleClick = useCallback(
      (event: React.MouseEvent, citationRef: string) => {
        event.stopPropagation();

        const citationNumber = parseInt(citationRef.replace(/[[\]]/g, ''), 10);
        const citation = citationMap[citationNumber];

        if (citation?.metadata?.recordId) {
          try {
            const recordCitations = aggregatedCitations[citation.metadata.recordId] || [];
            const isExcelOrCSV = ['csv', 'xlsx', 'xls'].includes(citation.metadata?.extension);
            onViewPdf('', citation, recordCitations, isExcelOrCSV);
          } catch (err) {
            console.error('Failed to fetch document:', err);
          }
        }
        handleCloseHoverCard();
      },
      [citationMap, aggregatedCitations, onViewPdf, handleCloseHoverCard]
    );

    const renderContentPart = useCallback(
      (part: string, index: number) => {
        const citationMatch = part.match(/\[(\d+)\]/);
        if (citationMatch) {
          const citationNumber = parseInt(citationMatch[1], 10);
          const citation = citationMap[citationNumber];
          const citationId = `citation-${citationNumber}-${index}-${messageId}`;

          if (!citation) {
            return <Fragment key={index}>{part}</Fragment>;
          }

          return (
            <Box
              key={citationId}
              component="span"
              onMouseEnter={(e) => handleMouseEnter(e, part, citationId)}
              onClick={(e) => handleClick(e, part)}
              onMouseLeave={handleMouseLeave}
              sx={{
                display: 'inline-flex',
                alignItems: 'center',
                ml: 0.5,
                mr: 0.25,
                cursor: 'pointer',
                position: 'relative',
                '&:hover': {
                  '& .citation-number': {
                    transform: 'scale(1.15) translateY(-1px)',
                    bgcolor: 'primary.main',
                    color: 'white',
                    boxShadow: '0 3px 8px rgba(25, 118, 210, 0.3)',
                  },
                },
                '&::after': {
                  content: '""',
                  position: 'absolute',
                  top: -8,
                  right: -8,
                  bottom: -8,
                  left: -8,
                  zIndex: -1,
                },
              }}
            >
              <Box
                component="span"
                className={`citation-number citation-number-${citationId}`}
                sx={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  width: '18px',
                  height: '18px',
                  borderRadius: '50%',
                  bgcolor: 'rgba(25, 118, 210, 0.08)',
                  color: 'primary.main',
                  fontSize: '0.65rem',
                  fontWeight: 600,
                  transition: 'all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1)',
                  textDecoration: 'none',
                  boxShadow: '0 1px 3px rgba(0,0,0,0.06)',
                  border: '1px solid',
                  borderColor: 'rgba(25, 118, 210, 0.12)',
                }}
              >
                {citationNumber}
              </Box>
            </Box>
          );
        }
        return <Fragment key={index}>{part}</Fragment>;
      },
      [citationMap, handleMouseEnter, handleClick, handleMouseLeave, messageId]
    );

    const processChildrenForCitations = useCallback(
      (children: React.ReactNode): React.ReactNode =>
        React.Children.toArray(children).flatMap((child, childIndex) => {
          if (typeof child === 'string') {
            return child
              .split(/(\[\d+\])/g)
              .map((part, partIndex) => renderContentPart(part, childIndex * 1000 + partIndex));
          }
          return child;
        }),
      [renderContentPart]
    );

    return (
      <Box sx={{ position: 'relative' }}>
        {/* Streaming indicator */}
        {showStreamingIndicator && (
          <Box
            sx={{
              position: 'absolute',
              top: -8,
              right: -8,
              width: 8,
              height: 8,
              borderRadius: '50%',
              bgcolor: 'success.main',
              animation: 'pulse 1.5s ease-in-out infinite',
              '@keyframes pulse': {
                '0%': { opacity: 1, transform: 'scale(1)' },
                '50%': { opacity: 0.5, transform: 'scale(1.2)' },
                '100%': { opacity: 1, transform: 'scale(1)' },
              },
            }}
          />
        )}

        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            p: ({ children }) => {
              const processedChildren = processChildrenForCitations(children);
              return (
                <Typography
                  component="p"
                  sx={{
                    mb: 1.5,
                    '&:last-child': { mb: 0 },
                    fontSize: '0.90rem',
                    lineHeight: 1.6,
                    letterSpacing: '0.01em',
                    wordBreak: 'break-word',
                    color: 'text.primary',
                    fontWeight: 400,
                  }}
                >
                  {processedChildren}
                </Typography>
              );
            },
            h1: ({ children }) => (
              <Typography variant="h3" sx={{ fontSize: '1.3rem', my: 2, fontWeight: 600 }}>
                {processChildrenForCitations(children)}
              </Typography>
            ),
            h2: ({ children }) => (
              <Typography variant="h4" sx={{ fontSize: '1.2rem', my: 2, fontWeight: 600 }}>
                {processChildrenForCitations(children)}
              </Typography>
            ),
            h3: ({ children }) => (
              <Typography variant="h4" sx={{ fontSize: '1.1rem', my: 1.5, fontWeight: 600 }}>
                {processChildrenForCitations(children)}
              </Typography>
            ),
            ul: ({ children }) => (
              <Box component="ul" sx={{ pl: 2.5, mb: 1.5, '& li': { mb: 0.5 } }}>
                {children}
              </Box>
            ),
            ol: ({ children }) => (
              <Box component="ol" sx={{ pl: 2.5, mb: 1.5, '& li': { mb: 0.5 } }}>
                {children}
              </Box>
            ),
            li: ({ children }) => {
              const processedChildren = processChildrenForCitations(children);
              return (
                <Typography component="li" sx={{ mb: 0.5, lineHeight: 1.6 }}>
                  {processedChildren}
                </Typography>
              );
            },
            code: ({ children, className }) => {
              const match = /language-(\w+)/.exec(className || '');

              // Inline code
              if (!match) {
                return (
                  <Box
                    component="code"
                    sx={{
                      bgcolor: (theme) =>
                        theme.palette.mode === 'dark'
                          ? 'rgba(255, 255, 255, 0.1)'
                          : 'rgba(0, 0, 0, 0.08)',
                      px: '0.4em',
                      py: '0.2em',
                      borderRadius: '4px',
                      fontFamily:
                        '"Fira Code", "JetBrains Mono", "SF Mono", Monaco, "Cascadia Code", "Roboto Mono", Consolas, "Courier New", monospace',
                      fontSize: '0.875em',
                      fontWeight: 500,
                      color: (theme) =>
                        theme.palette.mode === 'dark'
                          ? 'rgba(255, 255, 255, 0.9)'
                          : 'rgba(0, 0, 0, 0.8)',
                    }}
                  >
                    {children}
                  </Box>
                );
              }

              // Code block
              return (
                <Box
                  sx={{
                    bgcolor: (theme) =>
                      theme.palette.mode === 'dark' ? 'rgba(0, 0, 0, 0.4)' : 'rgba(0, 0, 0, 0.04)',
                    p: 2,
                    borderRadius: '8px',
                    fontFamily:
                      '"Fira Code", "JetBrains Mono", "SF Mono", Monaco, "Cascadia Code", "Roboto Mono", Consolas, "Courier New", monospace',
                    fontSize: '0.85em',
                    overflow: 'auto',
                    my: 2,
                    border: (theme) =>
                      `1px solid ${
                        theme.palette.mode === 'dark'
                          ? 'rgba(255, 255, 255, 0.1)'
                          : 'rgba(0, 0, 0, 0.1)'
                      }`,
                    position: 'relative',
                    '&::before': match
                      ? {
                          content: `"${match[1]}"`,
                          position: 'absolute',
                          top: '8px',
                          right: '12px',
                          fontSize: '0.75em',
                          color: 'text.secondary',
                          opacity: 0.7,
                          textTransform: 'uppercase',
                          fontWeight: 500,
                        }
                      : {},
                  }}
                >
                  <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordWrap: 'break-word' }}>
                    <code style={{ color: 'inherit' }}>{children}</code>
                  </pre>
                </Box>
              );
            },
            blockquote: ({ children }) => (
              <Box
                component="blockquote"
                sx={{
                  pl: 2,
                  py: 1,
                  my: 2,
                  borderLeft: (theme) => `4px solid ${theme.palette.primary.main}`,
                  bgcolor: (theme) =>
                    theme.palette.mode === 'dark'
                      ? 'rgba(33, 150, 243, 0.1)'
                      : 'rgba(25, 118, 210, 0.05)',
                  fontStyle: 'italic',
                  '& p': { mb: 0 },
                }}
              >
                {children}
              </Box>
            ),
            a: ({ href, children }) => (
              <Box
                component="a"
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                sx={{
                  color: 'primary.main',
                  textDecoration: 'underline',
                  '&:hover': {
                    textDecoration: 'none',
                  },
                }}
              >
                {children}
              </Box>
            ),
            strong: ({ children }) => (
              <Box component="strong" sx={{ fontWeight: 600 }}>
                {processChildrenForCitations(children)}
              </Box>
            ),
            em: ({ children }) => (
              <Box component="em" sx={{ fontStyle: 'italic' }}>
                {processChildrenForCitations(children)}
              </Box>
            ),
            hr: () => <Divider sx={{ my: 3 }} />,
          }}
          className="markdown-body"
        >
          {processedContent}
        </ReactMarkdown>

        <Popper
          open={Boolean(popperAnchor && hoveredCitationId)}
          anchorEl={popperAnchor}
          placement="bottom-start"
          modifiers={[
            { name: 'offset', options: { offset: [0, 12] } },
            {
              name: 'flip',
              enabled: true,
              options: { altBoundary: true, rootBoundary: 'viewport', padding: 8 },
            },
            {
              name: 'preventOverflow',
              enabled: true,
              options: { altAxis: true, altBoundary: true, boundary: 'viewport', padding: 16 },
            },
          ]}
          sx={{ zIndex: 9999, maxWidth: '95vw', width: '380px' }}
        >
          <ClickAwayListener onClickAway={handleCloseHoverCard}>
            <Box
              onMouseEnter={handleHoverCardMouseEnter}
              onMouseLeave={handleMouseLeave}
              sx={{ pointerEvents: 'auto' }}
            >
              {hoveredCitation && (
                <CitationHoverCard
                  citation={hoveredCitation}
                  isVisible={Boolean(hoveredCitationId)}
                  onRecordClick={(record) => {
                    handleCloseHoverCard();
                    onRecordClick(record);
                  }}
                  onClose={handleCloseHoverCard}
                  aggregatedCitations={hoveredRecordCitations}
                  onViewPdf={onViewPdf}
                />
              )}
            </Box>
          </ClickAwayListener>
        </Popper>
      </Box>
    );
  }
);

const ChatMessage = React.memo(
  ({
    message,
    index,
    onRegenerate,
    onFeedbackSubmit,
    conversationId,
    isRegenerating,
    showRegenerate,
    onViewPdf,
  }: ChatMessageProps) => {
    const theme = useTheme();
    const [isExpanded, setIsExpanded] = useState(false);
    const [selectedRecord, setSelectedRecord] = useState<Record | null>(null);
    const [isRecordDialogOpen, setRecordDialogOpen] = useState<boolean>(false);

    const isStreamingMessage = message.id.startsWith('streaming-');

    const aggregatedCitations = useMemo(() => {
      if (!message.citations) return {};

      return message.citations.reduce<{ [key: string]: CustomCitation[] }>((acc, citation) => {
        const recordId = citation.metadata?.recordId;
        if (!recordId) return acc;

        if (!acc[recordId]) {
          acc[recordId] = [];
        }
        acc[recordId].push(citation);
        return acc;
      }, {});
    }, [message.citations]);

    const handleToggleCitations = useCallback(() => {
      setIsExpanded((prev) => !prev);
    }, []);

    const handleOpenRecordDetails = useCallback(
      (record: Record) => {
        const recordCitations = aggregatedCitations[record.recordId] || [];
        setSelectedRecord({ ...record, citations: recordCitations });
        setRecordDialogOpen(true);
      },
      [aggregatedCitations]
    );

    const handleCloseRecordDetails = useCallback(() => {
      setRecordDialogOpen(false);
      setSelectedRecord(null);
    }, []);

    const handleViewPdf = useCallback(
      async (
        url: string,
        citation: CustomCitation,
        citations: CustomCitation[],
        isExcelFile?: boolean,
        buffer?: ArrayBuffer
      ): Promise<void> =>
        new Promise<void>((resolve) => {
          onViewPdf(url, citation, citations, isExcelFile, buffer);
          resolve();
        }),
      [onViewPdf]
    );

    const handleViewCitations = useCallback(
      async (recordId: string): Promise<void> =>
        new Promise<void>((resolve) => {
          const recordCitations = aggregatedCitations[recordId] || [];
          if (recordCitations.length > 0) {
            const citation = recordCitations[0];
            onViewPdf('', citation, recordCitations, false);
            resolve();
          }
        }),
      [aggregatedCitations, onViewPdf]
    );

    return (
      <Box sx={{ mb: 3, width: '100%', position: 'relative' }}>
        <Box
          sx={{
            mb: 1,
            display: 'flex',
            justifyContent: message.type === 'user' ? 'flex-end' : 'flex-start',
            px: 1,
            opacity: isRegenerating ? 0.6 : 1,
            transition: 'opacity 0.3s ease',
          }}
        >
          <Stack
            direction="row"
            spacing={1.5}
            alignItems="center"
            sx={{
              px: 1.5,
              py: 0.5,
              borderRadius: 1.5,
              backgroundColor: (themeVal) =>
                themeVal.palette.mode === 'dark'
                  ? 'rgba(255, 255, 255, 0.03)'
                  : 'rgba(0, 0, 0, 0.03)',
              border: (themeVal) =>
                `1px solid ${
                  themeVal.palette.mode === 'dark'
                    ? 'rgba(255, 255, 255, 0.08)'
                    : 'rgba(0, 0, 0, 0.08)'
                }`,
              backdropFilter: 'blur(8px)',
            }}
          >
            <Box
              sx={{
                width: 24,
                height: 24,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                borderRadius: '50%',
                backgroundColor: (themeVal) =>
                  message.type === 'user'
                    ? themeVal.palette.primary.main
                    : themeVal.palette.success.main,
                flexShrink: 0,
                boxShadow: (themeVal) =>
                  themeVal.palette.mode === 'dark'
                    ? '0 2px 8px rgba(0, 0, 0, 0.4)'
                    : '0 2px 8px rgba(0, 0, 0, 0.15)',
              }}
            >
              <Icon
                icon={message.type === 'user' ? accountIcon : 'lucide:sparkles'}
                width={12}
                height={12}
                color="white"
              />
            </Box>

            <Typography
              variant="caption"
              sx={{
                color: (themeVal) =>
                  themeVal.palette.mode === 'dark'
                    ? theme.palette.text.secondary
                    : 'rgba(0, 0, 0, 0.7)',
                fontSize: '0.75rem',
                fontWeight: 500,
                lineHeight: 1.2,
                letterSpacing: '0.2px',
              }}
            >
              {formatDate(message.createdAt)} • {formatTime(message.createdAt)}
            </Typography>

            {message.type === 'bot' &&
              message.confidence &&
              !isStreamingMessage &&
              message.confidence.trim() !== '' && (
                <Box
                  sx={{
                    px: 1.25,
                    py: 0.25,
                    borderRadius: 1.5,
                    backgroundColor: (themeVal) => {
                      const isHighConfidence = message.confidence === 'Very High';
                      const baseColor = isHighConfidence
                        ? themeVal.palette.success.main
                        : themeVal.palette.warning.main;
                      return themeVal.palette.mode === 'dark' ? `${baseColor}20` : `${baseColor}15`;
                    },
                    border: (themeVal) => {
                      const isHighConfidence = message.confidence === 'Very High';
                      const baseColor = isHighConfidence
                        ? themeVal.palette.success.main
                        : themeVal.palette.warning.main;
                      return `1px solid ${themeVal.palette.mode === 'dark' ? `${baseColor}40` : `${baseColor}30`}`;
                    },
                  }}
                >
                  <Typography
                    variant="caption"
                    sx={{
                      color: (themeVal) => {
                        const isHighConfidence = message.confidence === 'Very High';
                        return isHighConfidence
                          ? themeVal.palette.success.main
                          : themeVal.palette.warning.main;
                      },
                      fontSize: '0.65rem',
                      fontWeight: 500,
                      lineHeight: 1,
                      textTransform: 'uppercase',
                      letterSpacing: '0.5px',
                    }}
                  >
                    {message.confidence}
                  </Typography>
                </Box>
              )}
          </Stack>
        </Box>

        <Box sx={{ position: 'relative' }}>
          <Paper
            elevation={0}
            sx={{
              width: '100%',
              maxWidth: message.type === 'user' ? '70%' : '90%',
              p: message.type === 'user' ? 1.5 : 2,
              ml: message.type === 'user' ? 'auto' : 0,
              bgcolor: (themeVal) => {
                if (message.type === 'user') {
                  return themeVal.palette.mode === 'dark'
                    ? 'rgba(33, 150, 243, 0.1)'
                    : 'rgba(25, 118, 210, 0.08)';
                }
                return themeVal.palette.mode === 'dark'
                  ? 'rgba(255, 255, 255, 0.02)'
                  : 'rgba(0, 0, 0, 0.02)';
              },
              color: 'text.primary',
              borderRadius: 3,
              border: '1px solid',
              borderColor: (themeVal) => {
                if (message.type === 'user') {
                  return themeVal.palette.mode === 'dark'
                    ? alpha(themeVal.palette.primary.main, 0.4)
                    : alpha(themeVal.palette.primary.main, 0.3);
                }
                return themeVal.palette.mode === 'dark'
                  ? 'rgba(255, 255, 255, 0.1)'
                  : 'rgba(0, 0, 0, 0.1)';
              },
              position: 'relative',
              transition: 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
              opacity: isRegenerating ? 0.5 : 1,
              filter: isRegenerating ? 'blur(0.5px)' : 'none',
              fontFamily:
                '"Inter", "SF Pro Display", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
              boxShadow: (themeVal) =>
                themeVal.palette.mode === 'dark'
                  ? '0 4px 20px rgba(0, 0, 0, 0.15)'
                  : '0 2px 12px rgba(0, 0, 0, 0.08)',
              '&:hover': {
                borderColor: (themeVal) => {
                  if (message.type === 'user') {
                    return themeVal.palette.mode === 'dark'
                      ? alpha(themeVal.palette.primary.main, 0.6)
                      : alpha(themeVal.palette.primary.main, 0.5);
                  }
                  return themeVal.palette.mode === 'dark'
                    ? 'rgba(255, 255, 255, 0.15)'
                    : 'rgba(0, 0, 0, 0.15)';
                },
                boxShadow: (themeVal) =>
                  themeVal.palette.mode === 'dark'
                    ? '0 8px 32px rgba(0, 0, 0, 0.2)'
                    : '0 4px 20px rgba(0, 0, 0, 0.12)',
                transform: 'translateY(-1px)',
              },
            }}
          >
            {message.type === 'bot' ? (
              <StreamingContent
                messageId={message.id}
                fallbackContent={message.content}
                fallbackCitations={message.citations || []}
                onRecordClick={handleOpenRecordDetails}
                aggregatedCitations={aggregatedCitations}
                onViewPdf={handleViewPdf}
              />
            ) : (
              <Box
                sx={{
                  fontSize: '14px',
                  lineHeight: 1.6,
                  letterSpacing: '0.1px',
                  wordBreak: 'break-word',
                  fontFamily:
                    '"Inter", "SF Pro Display", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
                  color: (themeVal) =>
                    themeVal.palette.mode === 'dark'
                      ? 'rgba(255, 255, 255, 0.95)'
                      : 'rgba(0, 0, 0, 0.87)',
                }}
              >
                <ReactMarkdown>{message.content}</ReactMarkdown>
              </Box>
            )}

            {message.citations && message.citations.length > 0 && (
              <Box sx={{ mt: 2 }}>
                <Tooltip title={isExpanded ? 'Hide Citations' : 'Show Citations'}>
                  <Button
                    variant="outlined"
                    size="small"
                    onClick={handleToggleCitations}
                    startIcon={
                      <Icon icon={isExpanded ? downIcon : rightIcon} width={16} height={16} />
                    }
                    sx={{
                      color: (themeVal) =>
                        themeVal.palette.mode === 'dark'
                          ? themeVal.palette.primary.light
                          : themeVal.palette.primary.main,
                      textTransform: 'none',
                      fontWeight: 500,
                      fontSize: '11px',
                      fontFamily:
                        '"Inter", "SF Pro Display", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
                      py: 0.5,
                      px: 1.5,
                      borderRadius: 2,
                      borderColor: (themeVal) =>
                        themeVal.palette.mode === 'dark'
                          ? alpha(themeVal.palette.primary.main, 0.3)
                          : alpha(themeVal.palette.primary.main, 0.25),
                      backgroundColor: (themeVal) =>
                        themeVal.palette.mode === 'dark'
                          ? alpha(themeVal.palette.primary.main, 0.08)
                          : alpha(themeVal.palette.primary.main, 0.05),
                      '&:hover': {
                        backgroundColor: (themeVal) =>
                          themeVal.palette.mode === 'dark'
                            ? alpha(themeVal.palette.primary.main, 0.15)
                            : alpha(themeVal.palette.primary.main, 0.1),
                        borderColor: (themeVal) =>
                          themeVal.palette.mode === 'dark'
                            ? alpha(themeVal.palette.primary.main, 0.5)
                            : alpha(themeVal.palette.primary.main, 0.4),
                        transform: 'translateY(-1px)',
                      },
                    }}
                  >
                    {message.citations.length}{' '}
                    {message.citations.length === 1 ? 'Source' : 'Sources'}
                  </Button>
                </Tooltip>

                <Collapse in={isExpanded}>
                  <Box sx={{ mt: 2 }}>
                    {message.citations.map((citation, cidx) => (
                      <Paper
                        key={cidx}
                        elevation={0}
                        sx={{
                          p: 1.5,
                          mb: 1.5,
                          bgcolor: (themeVal) =>
                            themeVal.palette.mode === 'dark'
                              ? 'rgba(255, 255, 255, 0.03)'
                              : 'rgba(0, 0, 0, 0.02)',
                          borderRadius: 2,
                          border: '1px solid',
                          borderColor: (themeVal) =>
                            themeVal.palette.mode === 'dark'
                              ? 'rgba(255, 255, 255, 0.08)'
                              : 'rgba(0, 0, 0, 0.08)',
                          fontFamily:
                            '"Inter", "SF Pro Display", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
                          transition: 'all 0.2s ease',
                          '&:hover': {
                            borderColor: (themeVal) =>
                              themeVal.palette.mode === 'dark'
                                ? 'rgba(255, 255, 255, 0.12)'
                                : 'rgba(0, 0, 0, 0.12)',
                            backgroundColor: (themeVal) =>
                              themeVal.palette.mode === 'dark'
                                ? 'rgba(255, 255, 255, 0.05)'
                                : 'rgba(0, 0, 0, 0.03)',
                          },
                        }}
                      >
                        <Box
                          sx={{
                            pl: 1.5,
                            borderLeft: (themeVal) => `3px solid ${themeVal.palette.primary.main}`,
                            borderRadius: '2px',
                          }}
                        >
                          <Typography
                            sx={{
                              fontSize: '13px',
                              lineHeight: 1.6,
                              color: (themeVal) =>
                                themeVal.palette.mode === 'dark'
                                  ? 'rgba(255, 255, 255, 0.85)'
                                  : 'rgba(0, 0, 0, 0.75)',
                              fontStyle: 'normal',
                              fontWeight: 400,
                              mb: 1.5,
                              fontFamily:
                                '"Inter", "SF Pro Display", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
                            }}
                          >
                            {citation.metadata?.blockText &&
                            citation.metadata?.extension === 'pdf' &&
                            typeof citation.metadata?.blockText === 'string' &&
                            citation.metadata?.blockText.length > 0
                              ? citation.metadata?.blockText
                              : citation.content}
                          </Typography>

                          {citation.metadata?.recordId && (
                            <Box
                              sx={{
                                display: 'flex',
                                justifyContent: 'flex-end',
                                gap: 1,
                                pt: 1,
                              }}
                            >
                              {isDocViewable(citation.metadata.extension) && (
                                <Button
                                  size="small"
                                  variant="text"
                                  startIcon={<Icon icon={eyeIcon} width={14} height={14} />}
                                  onClick={() => handleViewCitations(citation.metadata?.recordId)}
                                  sx={{
                                    textTransform: 'none',
                                    fontSize: '11px',
                                    fontWeight: 500,
                                    borderRadius: 1,
                                    px: 1.5,
                                    py: 0.25,
                                  }}
                                >
                                  View Citations
                                </Button>
                              )}
                              <Button
                                size="small"
                                variant="text"
                                startIcon={<Icon icon={fileDocIcon} width={14} height={14} />}
                                onClick={() => {
                                  if (citation.metadata?.recordId) {
                                    handleOpenRecordDetails({
                                      ...citation.metadata,
                                      citations: [],
                                    });
                                  }
                                }}
                                sx={{
                                  textTransform: 'none',
                                  fontSize: '11px',
                                  fontWeight: 500,
                                  borderRadius: 1,
                                  px: 1.5,
                                  py: 0.25,
                                }}
                              >
                                Details
                              </Button>
                            </Box>
                          )}
                        </Box>
                      </Paper>
                    ))}
                  </Box>
                </Collapse>

                {isExpanded && (
                  <Tooltip title="Hide Citations">
                    <Button
                      variant="text"
                      size="small"
                      onClick={handleToggleCitations}
                      startIcon={<Icon icon={upIcon} width={16} height={16} />}
                      sx={{
                        color: (themeVal) =>
                          themeVal.palette.mode === 'dark'
                            ? themeVal.palette.primary.light
                            : themeVal.palette.primary.main,
                        textTransform: 'none',
                        fontWeight: 500,
                        fontSize: '11px',
                        borderRadius: 1,
                        px: 1.5,
                        py: 0.25,
                      }}
                    >
                      Hide citations
                    </Button>
                  </Tooltip>
                )}
              </Box>
            )}

            {message.type === 'bot' && !isStreamingMessage && (
              <>
                <Divider
                  sx={{
                    my: 2,
                    borderColor: (themeVal) =>
                      themeVal.palette.mode === 'dark'
                        ? 'rgba(255, 255, 255, 0.08)'
                        : 'rgba(0, 0, 0, 0.08)',
                  }}
                />
                <Stack direction="row" spacing={1.5} alignItems="center">
                  {showRegenerate && (
                    <>
                      <Tooltip title="Regenerate response">
                        <IconButton
                          onClick={() => onRegenerate(message.id)}
                          size="small"
                          disabled={isRegenerating}
                          sx={{
                            borderRadius: 1.5,
                            p: 1,
                            backgroundColor: (themeVal) =>
                              themeVal.palette.mode === 'dark'
                                ? 'rgba(255, 255, 255, 0.05)'
                                : 'rgba(0, 0, 0, 0.04)',
                            border: (themeVal) =>
                              `1px solid ${
                                themeVal.palette.mode === 'dark'
                                  ? 'rgba(255, 255, 255, 0.1)'
                                  : 'rgba(0, 0, 0, 0.08)'
                              }`,
                            '&:hover': {
                              backgroundColor: (themeVal) =>
                                themeVal.palette.mode === 'dark'
                                  ? 'rgba(255, 255, 255, 0.08)'
                                  : 'rgba(0, 0, 0, 0.06)',
                            },
                          }}
                        >
                          <Icon
                            icon={isRegenerating ? loadingIcon : refreshIcon}
                            width={16}
                            height={16}
                            className={isRegenerating ? 'spin' : ''}
                          />
                        </IconButton>
                      </Tooltip>
                      <MessageFeedback
                        messageId={message.id}
                        conversationId={conversationId}
                        onFeedbackSubmit={onFeedbackSubmit}
                      />
                    </>
                  )}
                </Stack>
              </>
            )}
          </Paper>
        </Box>

        <Dialog
          open={isRecordDialogOpen}
          onClose={handleCloseRecordDetails}
          maxWidth="md"
          fullWidth
          PaperProps={{
            sx: {
              borderRadius: 3,
              bgcolor: (themeVal) =>
                themeVal.palette.mode === 'dark'
                  ? 'rgba(18, 18, 18, 0.95)'
                  : 'rgba(255, 255, 255, 0.95)',
              backdropFilter: 'blur(12px)',
            },
          }}
        >
          <DialogTitle
            sx={{
              fontFamily:
                '"Inter", "SF Pro Display", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
              fontWeight: 600,
            }}
          >
            Record Details
          </DialogTitle>
          <DialogContent>
            {selectedRecord && (
              <RecordDetails
                recordId={selectedRecord.recordId}
                citations={selectedRecord.citations}
              />
            )}
          </DialogContent>
        </Dialog>

        {isRegenerating && (
          <Fade in>
            <Box
              sx={{
                position: 'absolute',
                top: '50%',
                left: '50%',
                transform: 'translate(-50%, -50%)',
                zIndex: 1,
                p: 2,
                borderRadius: 2,
                backgroundColor: (themeVal) =>
                  themeVal.palette.mode === 'dark'
                    ? 'rgba(0, 0, 0, 0.8)'
                    : 'rgba(255, 255, 255, 0.9)',
                backdropFilter: 'blur(8px)',
              }}
            >
              <CircularProgress
                size={24}
                thickness={4}
                sx={{
                  color: (themeVal) => themeVal.palette.primary.main,
                }}
              />
            </Box>
          </Fade>
        )}
      </Box>
    );
  },
  (prevProps, nextProps) =>
    prevProps.message.id === nextProps.message.id &&
    prevProps.message.content === nextProps.message.content &&
    prevProps.message.updatedAt?.getTime() === nextProps.message.updatedAt?.getTime() &&
    prevProps.showRegenerate === nextProps.showRegenerate &&
    prevProps.isRegenerating === nextProps.isRegenerating &&
    prevProps.conversationId === nextProps.conversationId
);

export default ChatMessage;
