// src/components/chat/chat-message.tsx

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

// This is the single source of truth for the context
export const StreamingContext = createContext<StreamingContextType | null>(null);

export const useStreamingContent = () => {
  const context = useContext(StreamingContext);
  if (!context) {
    throw new Error('useStreamingContent must be used within StreamingProvider');
  }
  return context;
};

// ... (Rest of the file is unchanged)
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

    const isStreaming = streamingState.messageId === messageId && streamingState.isActive;
    const displayContent = isStreaming ? streamingState.content : fallbackContent;
    const displayCitations = isStreaming ? streamingState.citations : fallbackCitations;

    const [hoveredCitationId, setHoveredCitationId] = useState<string | null>(null);
    const [hoveredRecordCitations, setHoveredRecordCitations] = useState<CustomCitation[]>([]);
    const hoverTimeoutRef = useRef<NodeJS.Timeout | null>(null);
    const [hoveredCitation, setHoveredCitation] = useState<CustomCitation | null>(null);

    const [popperAnchor, setPopperAnchor] = useState<null | {
      getBoundingClientRect: () => DOMRect;
    }>(null);

    const citationNumberMap = useMemo(() => {
      const result: { [key: number]: CustomCitation } = {};
      displayCitations.forEach((citation) => {
        if (citation && citation.chunkIndex && !result[citation.chunkIndex]) {
          result[citation.chunkIndex] = citation;
        }
      });
      return result;
    }, [displayCitations]);

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
        const citation = citationNumberMap[citationNumber];

        if (citation) {
          if (citation.metadata?.recordId) {
            const recordCitations = aggregatedCitations[citation.metadata.recordId] || [];
            setHoveredRecordCitations(recordCitations);
          }
          setHoveredCitation(citation);
          setHoveredCitationId(citationId);
        }
      },
      [citationNumberMap, aggregatedCitations]
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
        const citation = citationNumberMap[citationNumber];

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
      [citationNumberMap, aggregatedCitations, onViewPdf, handleCloseHoverCard]
    );

    const renderContentPart = (part: string, index: number) => {
      const citationMatch = part.match(/\[(\d+)\]/);
      if (citationMatch) {
        const citationNumber = parseInt(citationMatch[1], 10);
        const citation = citationNumberMap[citationNumber];
        const citationId = `citation-${citationNumber}-${index}`;

        if (!citation) return <Fragment key={index}>{part}</Fragment>;

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
    };

    return (
      <Box sx={{ position: 'relative' }}>
        {isStreaming && (
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
              const processedChildren = React.Children.toArray(children).flatMap((child) => {
                if (typeof child === 'string') {
                  return child.split(/(\[\d+\])/g).map(renderContentPart);
                }
                return child;
              });

              return (
                <Typography
                  component="p"
                  sx={{
                    mb: 2,
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
              <Typography variant="h1" sx={{ fontSize: '1.4rem', my: 2 }}>
                {children}
              </Typography>
            ),
            h2: ({ children }) => (
              <Typography variant="h2" sx={{ fontSize: '1.2rem', my: 2 }}>
                {children}
              </Typography>
            ),
            h3: ({ children }) => (
              <Typography variant="h3" sx={{ fontSize: '1.1rem', my: 1.5 }}>
                {children}
              </Typography>
            ),
            ul: ({ children }) => (
              <Box component="ul" sx={{ pl: 2.5, mb: 1.5 }}>
                {children}
              </Box>
            ),
            ol: ({ children }) => (
              <Box component="ol" sx={{ pl: 2.5, mb: 1.5 }}>
                {children}
              </Box>
            ),
            li: ({ children }) => {
              const processedChildren = React.Children.toArray(children).flatMap((child) => {
                if (typeof child === 'string') {
                  return child.split(/(\[\d+\])/g).map(renderContentPart);
                }
                if (React.isValidElement(child) && child.props.children) {
                  const grandChildren = React.Children.toArray(child.props.children).flatMap(
                    (grandChild) =>
                      typeof grandChild === 'string'
                        ? grandChild.split(/(\[\d+\])/g).map(renderContentPart)
                        : grandChild
                  );
                  return React.cloneElement(child, { ...child.props }, grandChildren);
                }
                return child;
              });
              return (
                <Typography component="li" sx={{ mb: 0.75 }}>
                  {processedChildren}
                </Typography>
              );
            },
            code: ({ children, className }) => {
              const match = /language-(\w+)/.exec(className || '');
              return !match ? (
                <Box
                  component="code"
                  sx={{
                    bgcolor: 'rgba(0, 0, 0, 0.04)',
                    px: '0.4em',
                    py: '0.2em',
                    borderRadius: '4px',
                    fontFamily: 'monospace',
                    fontSize: '0.9em',
                  }}
                >
                  {children}
                </Box>
              ) : (
                <Box
                  sx={{
                    bgcolor: 'rgba(0, 0, 0, 0.04)',
                    p: 1.5,
                    borderRadius: '4px',
                    fontFamily: 'monospace',
                    fontSize: '0.85em',
                    overflow: 'auto',
                    my: 1.5,
                  }}
                >
                  <pre style={{ margin: 0 }}>
                    <code>{children}</code>
                  </pre>
                </Box>
              );
            },
            a: ({ href, children }) => (
              <a href={href} target="_blank" rel="noopener noreferrer">
                {children}
              </a>
            ),
          }}
          className="markdown-body"
        >
          {displayContent}
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
            px: 1.5,
            opacity: isRegenerating ? 0.5 : 1,
            transition: 'opacity 0.2s ease-in-out',
          }}
        >
          <Stack
            direction="row"
            spacing={1}
            alignItems="center"
            sx={{
              px: 1,
              py: 0.25,
              borderRadius: '8px',
              backgroundColor: 'rgba(0, 0, 0, 0.02)',
            }}
          >
            <Icon
              icon={message.type === 'user' ? accountIcon : robotIcon}
              width={14}
              height={14}
              color={message.type === 'user' ? '#1976d2' : '#2e7d32'}
            />
            <Typography
              variant="caption"
              sx={{
                color: 'text.secondary',
                fontSize: '0.65rem',
                fontWeight: 500,
              }}
            >
              {formatDate(message.createdAt)} • {formatTime(message.createdAt)}
            </Typography>
            {message.type === 'bot' && message.confidence && (
              <Tooltip title="Confidence score" placement="top">
                <Chip
                  label={message.confidence}
                  size="small"
                  sx={{
                    height: '20px',
                    fontSize: '0.60rem',
                    fontWeight: 600,
                    backgroundColor: (themeVal) =>
                      message.confidence === 'Very High'
                        ? themeVal.palette.success.dark
                        : themeVal.palette.warning.dark,
                    color: (themeVal) => themeVal.palette.common.white,
                    border: (themeVal) =>
                      `1px solid ${
                        message.confidence === 'Very High'
                          ? themeVal.palette.success.main
                          : themeVal.palette.warning.main
                      }`,
                    '& .MuiChip-label': {
                      px: 1,
                      py: 0.25,
                    },
                    '&:hover': {
                      backgroundColor: (themeVal) =>
                        message.confidence === 'Very High'
                          ? themeVal.palette.success.main
                          : themeVal.palette.warning.main,
                    },
                  }}
                />
              </Tooltip>
            )}
          </Stack>
        </Box>

        <Box sx={{ position: 'relative' }}>
          <Paper
            elevation={0}
            sx={{
              width: '100%',
              maxWidth: '80%',
              p: 2,
              ml: message.type === 'user' ? 'auto' : 0,
              bgcolor: (themeVal) => {
                if (message.type === 'user') {
                  return themeVal.palette.mode === 'dark' ? '#3a3d42' : '#e3f2fd';
                }
                return themeVal.palette.mode === 'dark' ? '#2a2d32' : '#f8f9fa';
              },
              color: 'text.primary',
              borderRadius: '8px',
              border: '1px solid',
              borderColor: (themeVal) => {
                if (message.type === 'user') {
                  return themeVal.palette.mode === 'dark'
                    ? alpha(themeVal.palette.primary.main, 0.3)
                    : alpha(themeVal.palette.primary.main, 0.2);
                }
                return themeVal.palette.mode === 'dark' ? '#404448' : '#e1e5e9';
              },
              position: 'relative',
              transition: 'all 0.2s ease-in-out',
              opacity: isRegenerating ? 0.5 : 1,
              filter: isRegenerating ? 'blur(0.5px)' : 'none',
              fontFamily: '"Segoe UI", Tahoma, Geneva, Verdana, sans-serif',
              '&:hover': {
                borderColor: (themeVal) => {
                  if (message.type === 'user') {
                    return themeVal.palette.mode === 'dark'
                      ? alpha(themeVal.palette.primary.main, 0.4)
                      : alpha(themeVal.palette.primary.main, 0.3);
                  }
                  return themeVal.palette.mode === 'dark' ? '#484b52' : '#dee2e6';
                },
                boxShadow: (themeVal) =>
                  themeVal.palette.mode === 'dark'
                    ? '0 2px 8px rgba(0, 0, 0, 0.3)'
                    : '0 2px 8px rgba(0, 0, 0, 0.05)',
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
                  letterSpacing: '0.2px',
                  wordBreak: 'break-word',
                  fontFamily: '"Segoe UI", Tahoma, Geneva, Verdana, sans-serif',
                  color: (themeVal) => (themeVal.palette.mode === 'dark' ? '#e8eaed' : '#212529'),
                }}
              >
                <ReactMarkdown>{message.content}</ReactMarkdown>
              </Box>
            )}

            {message.citations && message.citations.length > 0 && (
              <Box sx={{ mt: 2 }}>
                <Tooltip title={isExpanded ? 'Hide Citations' : 'Show Citations'}>
                  <Button
                    variant="text"
                    size="small"
                    onClick={handleToggleCitations}
                    startIcon={
                      <Icon icon={isExpanded ? downIcon : rightIcon} width={14} height={14} />
                    }
                    sx={{
                      color: (themeVal) =>
                        themeVal.palette.mode === 'dark'
                          ? themeVal.palette.primary.light
                          : themeVal.palette.primary.main,
                      textTransform: 'none',
                      fontWeight: 500,
                      fontSize: '11px',
                      fontFamily: '"Segoe UI", Tahoma, Geneva, Verdana, sans-serif',
                      py: 0.5,
                      px: 1.5,
                      borderRadius: '4px',
                      border: '1px solid',
                      borderColor: (themeVal) =>
                        themeVal.palette.mode === 'dark'
                          ? alpha(themeVal.palette.primary.main, 0.2)
                          : alpha(themeVal.palette.primary.main, 0.15),
                      backgroundColor: (themeVal) =>
                        themeVal.palette.mode === 'dark'
                          ? alpha(themeVal.palette.primary.main, 0.08)
                          : alpha(themeVal.palette.primary.main, 0.04),
                      '&:hover': {
                        backgroundColor: (themeVal) =>
                          themeVal.palette.mode === 'dark'
                            ? alpha(themeVal.palette.primary.main, 0.12)
                            : alpha(themeVal.palette.primary.main, 0.06),
                        borderColor: (themeVal) =>
                          themeVal.palette.mode === 'dark'
                            ? alpha(themeVal.palette.primary.main, 0.3)
                            : alpha(themeVal.palette.primary.main, 0.2),
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
                          p: 2,
                          mb: 2,
                          bgcolor: (themeVal) =>
                            themeVal.palette.mode === 'dark' ? '#2a2d32' : '#f8f9fa',
                          borderRadius: '6px',
                          border: '1px solid',
                          borderColor: (themeVal) =>
                            themeVal.palette.mode === 'dark' ? '#404448' : '#e1e5e9',
                          fontFamily: '"Segoe UI", Tahoma, Geneva, Verdana, sans-serif',
                        }}
                      >
                        <Box
                          sx={{
                            pl: 2,
                            borderLeft: (themeVal) => `3px solid ${themeVal.palette.primary.main}`,
                            borderRadius: '2px',
                          }}
                        >
                          <Typography
                            sx={{
                              fontSize: '13px',
                              lineHeight: 1.6,
                              color: (themeVal) =>
                                themeVal.palette.mode === 'dark' ? '#e8eaed' : '#495057',
                              fontStyle: 'normal',
                              fontWeight: 400,
                              mb: 2,
                              fontFamily: '"Segoe UI", Tahoma, Geneva, Verdana, sans-serif',
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
                                gap: 1.5,
                                pt: 1,
                              }}
                            >
                              {isDocViewable(citation.metadata.extension) && (
                                <Button
                                  size="small"
                                  variant="text"
                                  startIcon={<Icon icon={eyeIcon} width={14} height={14} />}
                                  onClick={() => handleViewCitations(citation.metadata?.recordId)}
                                  sx={{ textTransform: 'none', fontSize: '11px', fontWeight: 500 }}
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
                                sx={{ textTransform: 'none', fontSize: '11px', fontWeight: 500 }}
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
                      startIcon={<Icon icon={upIcon} width={14} height={14} />}
                      sx={{
                        color: (themeVal) =>
                          themeVal.palette.mode === 'dark'
                            ? themeVal.palette.primary.light
                            : themeVal.palette.primary.main,
                        textTransform: 'none',
                        fontWeight: 500,
                        fontSize: '11px',
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
                <Divider sx={{ my: 1, borderColor: (t) => t.palette.divider }} />
                <Stack direction="row" spacing={1} alignItems="center">
                  {showRegenerate && (
                    <>
                      <Tooltip title="Regenerate response">
                        <IconButton
                          onClick={() => onRegenerate(message.id)}
                          size="small"
                          disabled={isRegenerating}
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
          PaperProps={{ sx: { borderRadius: '12px' } }}
        >
          <DialogTitle>Record Details</DialogTitle>
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
              }}
            >
              <CircularProgress size={24} />
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
