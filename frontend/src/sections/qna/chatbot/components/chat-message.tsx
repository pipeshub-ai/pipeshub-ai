import type { Metadata, CustomCitation } from 'src/types/chat-bot';
import type { Record, ChatMessageProps, MessageContentProps } from 'src/types/chat-message';

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
import React, { useRef, useMemo, useState, useCallback } from 'react';

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

const MessageContent: React.FC<MessageContentProps> = ({
  content,
  citations,
  onRecordClick,
  aggregatedCitations,
  onViewPdf,
}) => {
  // We track the hovered citation by a unique identifier: "lineIndex-partIndex"
  const [hoveredCitationId, setHoveredCitationId] = useState<string | null>(null);
  const [hoveredRecordCitations, setHoveredRecordCitations] = useState<CustomCitation[]>([]);
  const hoverTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  // Create a map of refs for each citation number
  const citationRefs = useRef<{ [key: string]: HTMLElement | null }>({});

  // Current citation data
  const [hoveredCitation, setHoveredCitation] = useState<CustomCitation | null>(null);

  // Create a mapping from citation number to the actual citation object
  const citationNumberMap = useMemo(() => {
    const result: { [key: number]: CustomCitation } = {};

    citations.forEach((citation) => {
      if (citation && citation.chunkIndex && !result[citation.chunkIndex]) {
        result[citation.chunkIndex] = citation;
      }
    });

    return result;
  }, [citations]);

  // Split content by newlines first
  const lines = useMemo(() => content.split('\n'), [content]);

  const handleCitationInteraction = useCallback(
    (citationRef: string, citationId: string) => {
      if (hoverTimeoutRef.current) {
        clearTimeout(hoverTimeoutRef.current);
      }

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

  const handleMouseEnter = useCallback(
    (citationRef: string, citationId: string) => {
      handleCitationInteraction(citationRef, citationId);
    },
    [handleCitationInteraction]
  );

  const handleMouseLeave = useCallback(() => {
    hoverTimeoutRef.current = setTimeout(() => {
      setHoveredCitationId(null);
      setHoveredRecordCitations([]);
      setHoveredCitation(null);
    }, 300); // Increased from 100ms to 300ms for smoother hover
  }, []);

  const handleHoverCardMouseEnter = useCallback(() => {
    if (hoverTimeoutRef.current) {
      clearTimeout(hoverTimeoutRef.current);
    }
  }, []);

  const handleCloseHoverCard = useCallback(() => {
    setHoveredCitationId(null);
    setHoveredRecordCitations([]);
    setHoveredCitation(null);
  }, []);

  const handleClick = useCallback(
    (citationRef: string, citationId: string, event: React.MouseEvent) => {
      // Prevent any parent click handlers from firing
      event.stopPropagation();
      console.log('citation clicked');

      // Get the citation number and citation object
      const citationNumber = parseInt(citationRef.replace(/[[\]]/g, ''), 10);
      const citation = citationNumberMap[citationNumber];

      if (citation) {
        let recordCitationsForDoc: CustomCitation[] = [];

        if (citation.metadata?.recordId) {
          const recordCitations = aggregatedCitations[citation.metadata.recordId] || [];
          recordCitationsForDoc = recordCitations;
          setHoveredRecordCitations(recordCitations);
        }
        setHoveredCitation(citation);
        setHoveredCitationId(citationId);

        // Open the document if it's a PDF, Excel, or CSV
        if (citation.metadata?.recordId) {
          try {
            const isExcelOrCSV = ['csv', 'xlsx', 'xls'].includes(citation.metadata?.extension);
            onViewPdf('', citation, recordCitationsForDoc, isExcelOrCSV);
          } catch (err) {
            console.error('Failed to fetch document:', err);
          }
        }
        console.log(recordCitationsForDoc);
      }

      // Toggle the hover card
      if (hoveredCitationId === citationId) {
        handleCloseHoverCard();
      }
    },
    [hoveredCitationId, citationNumberMap, aggregatedCitations, onViewPdf, handleCloseHoverCard]
  );

  // Render line with markdown and citations
  const renderLineWithMarkdown = (line: string, lineIndex: number) => {
    // Split the line by citation pattern
    const parts = line.split(/(\[\d+\])/);

    return (
      <Box
        key={lineIndex}
        sx={{
          mb: lineIndex < lines.length - 1 ? 2 : 0,
          opacity: 1,
          animation: lineIndex > 0 ? 'fadeIn 0.3s ease-in-out' : 'none',
          '@keyframes fadeIn': {
            from: { opacity: 0.7 },
            to: { opacity: 1 },
          },
        }}
      >
        {parts.map((part, partIndex) => {
          // Check if this part is a citation
          const citationMatch = part.match(/\[(\d+)\]/);
          if (citationMatch) {
            const citationNumber = parseInt(citationMatch[1], 10);
            const citation = citationNumberMap[citationNumber];
            const citationId = `${lineIndex}-${partIndex}`;
            if (!citation) return null;
            return (
              <Box
                component="span"
                onMouseEnter={() => handleMouseEnter(part, citationId)}
                onClick={(e) => handleClick(part, citationId, e)}
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
                  // Add invisible hit area for smoother hover
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
                  ref={(el: any) => {
                    citationRefs.current[citationId] = el;
                  }}
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

          // For regular text parts, render with markdown
          return (
            <Box
              component="span"
              key={`${lineIndex}-${partIndex}`}
              sx={{
                display: 'inline',
                '& img': {
                  maxWidth: '100%',
                  height: 'auto',
                  borderRadius: '8px',
                  my: 2,
                  boxShadow: '0 2px 12px rgba(0,0,0,0.06)',
                },
                '& ul, & ol': {
                  pl: 2.5,
                  mb: 2,
                  mt: 1,
                },
                '& li': {
                  mb: 0.75,
                },
                // To prevent ReactMarkdown from creating unwanted paragraph elements
                '& > div > p': {
                  display: 'inline',
                  margin: 0,
                },
              }}
            >
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  // Override paragraph to render as inline span for text segments
                  p: ({ node, ...props }) => <span {...props} />,
                }}
              >
                {part}
              </ReactMarkdown>
            </Box>
          );
        })}
      </Box>
    );
  };

  return (
    <Box sx={{ position: 'relative' }}>
      <Typography
        component="div"
        sx={{
          fontSize: '0.90rem',
          lineHeight: 1.5,
          letterSpacing: '0.01em',
          wordBreak: 'break-word',
          color: 'text.primary',
          fontWeight: 400,
          '& code': {
            backgroundColor: 'rgba(0, 0, 0, 0.04)',
            padding: '0.2em 0.4em',
            borderRadius: '4px',
            fontFamily: 'monospace',
            fontSize: '0.9em',
          },
          '& pre': {
            backgroundColor: 'rgba(0, 0, 0, 0.04)',
            padding: 1.5,
            borderRadius: '4px',
            fontFamily: 'monospace',
            fontSize: '0.85em',
            overflow: 'auto',
            my: 1.5,
            '& code': {
              backgroundColor: 'transparent',
              padding: 0,
            },
          },
          '& strong': {
            fontWeight: 600,
            color: 'text.primary',
          },
          '& a': {
            color: 'primary.main',
            textDecoration: 'none',
            borderBottom: '1px dotted',
            borderColor: 'primary.light',
            transition: 'all 0.2s ease',
            '&:hover': {
              color: 'primary.dark',
              borderColor: 'primary.main',
            },
          },
          '& h1': {
            fontSize: '1.4rem',
            fontWeight: 600,
            marginTop: 2,
            marginBottom: 1.5,
          },
          '& h2': {
            fontSize: '1.2rem',
            fontWeight: 600,
            marginTop: 2,
            marginBottom: 1.5,
          },
          '& h3': {
            fontSize: '1.1rem',
            fontWeight: 600,
            marginTop: 2,
            marginBottom: 1,
          },
          '& h4, & h5, & h6': {
            fontSize: '1rem',
            fontWeight: 600,
            marginTop: 1.5,
            marginBottom: 1,
          },
          '& ul, & ol': {
            paddingLeft: 2.5,
            marginBottom: 1.5,
            marginTop: 0.5,
          },
          '& li': {
            marginBottom: 0.75,
          },
          '& blockquote': {
            borderLeft: '4px solid',
            borderColor: 'divider',
            paddingLeft: 2,
            margin: '1em 0',
            color: 'text.secondary',
            fontStyle: 'italic',
          },
          '& table': {
            borderCollapse: 'collapse',
            width: '100%',
            marginBottom: 2,
          },
          '& th, & td': {
            border: '1px solid',
            borderColor: 'divider',
            padding: '8px 12px',
            textAlign: 'left',
          },
          '& th': {
            backgroundColor: 'rgba(0, 0, 0, 0.02)',
            fontWeight: 600,
          },
        }}
      >
        {lines.map((line, lineIndex) => renderLineWithMarkdown(line, lineIndex))}
      </Typography>

      {/* Popper for Citation Hover Card */}
      {hoveredCitationId && hoveredCitation && (
        <Popper
          open={Boolean(hoveredCitationId)}
          anchorEl={citationRefs.current[hoveredCitationId]}
          placement="bottom-start"
          modifiers={[
            {
              name: 'offset',
              options: {
                offset: [0, 8], // x, y offset
              },
            },
            {
              name: 'flip',
              enabled: true,
              options: {
                altBoundary: true,
                rootBoundary: 'viewport',
                padding: 8,
              },
            },
            {
              name: 'preventOverflow',
              enabled: true,
              options: {
                altAxis: true,
                altBoundary: true,
                boundary: 'viewport',
                padding: 16,
              },
            },
          ]}
          sx={{
            zIndex: 9999,
            maxWidth: '95vw',
            width: '380px',
            // Important to prevent layout shifts
            position: 'fixed',
            pointerEvents: 'none', // This ensures the popper doesn't affect layout
          }}
        >
          <ClickAwayListener onClickAway={handleCloseHoverCard}>
            <Box
              onMouseEnter={handleHoverCardMouseEnter}
              onMouseLeave={handleMouseLeave}
              sx={{
                pointerEvents: 'auto', // Re-enable pointer events for the card itself
              }}
            >
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
            </Box>
          </ClickAwayListener>
        </Popper>
      )}
    </Box>
  );
};

const ChatMessage = ({
  message,
  isExpanded,
  onToggleCitations,
  index,
  onRegenerate,
  onFeedbackSubmit,
  conversationId,
  isRegenerating,
  showRegenerate,
  onViewPdf,
}: ChatMessageProps) => {
  const [selectedRecord, setSelectedRecord] = useState<Record | null>(null);
  const [isRecordDialogOpen, setRecordDialogOpen] = useState<boolean>(false);
  const theme = useTheme();
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

  const handleOpenRecordDetails = (record: Record) => {
    const recordCitations = aggregatedCitations[record.recordId] || [];
    setSelectedRecord({ ...record, citations: recordCitations });
    setRecordDialogOpen(true);
  };

  const handleCloseRecordDetails = () => {
    setRecordDialogOpen(false);
    setSelectedRecord(null);
  };

  const handleViewPdf = async (
    url: string,
    citation: CustomCitation,
    citations: CustomCitation[],
    isExcelFile?: boolean,
    buffer?: ArrayBuffer
  ): Promise<void> =>
    new Promise<void>((resolve) => {
      onViewPdf(url, citation, citations, isExcelFile, buffer);
      resolve();
    });

  const handleViewCitations = async (recordId: string): Promise<void> =>
    new Promise<void>((resolve) => {
      const recordCitations = aggregatedCitations[recordId] || [];
      if (recordCitations.length > 0) {
        const citationMeta = recordCitations[0].metadata;
        const citation = recordCitations[0];
        onViewPdf('', citation, recordCitations, false);
        resolve();
      }
    });

  return (
    <Box sx={{ mb: 3, width: '100%', position: 'relative' }}>
      {/* Message Metadata */}
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

      {/* Message Content */}
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
        boxShadow: (themeVal) => themeVal.palette.mode === 'dark'
          ? '0 2px 8px rgba(0, 0, 0, 0.3)'
          : '0 2px 8px rgba(0, 0, 0, 0.05)',
      },
    }}
  >
    {/* Message Content with Citation Hover */}
    {message.type === 'bot' ? (
      <MessageContent
        content={message.content}
        citations={message.citations || []}
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
          color: (themeVal) => themeVal.palette.mode === 'dark' ? '#e8eaed' : '#212529',
          '& p': { mt: 0, mb: 1.5 },
          '& h1': { 
            fontSize: '1.3rem', 
            fontWeight: 600, 
            my: 1.5,
            color: (themeVal) => themeVal.palette.mode === 'dark' ? '#e8eaed' : '#212529',
          },
          '& h2': { 
            fontSize: '1.15rem', 
            fontWeight: 600, 
            my: 1.5,
            color: (themeVal) => themeVal.palette.mode === 'dark' ? '#e8eaed' : '#212529',
          },
          '& h3': { 
            fontSize: '1.05rem', 
            fontWeight: 600, 
            my: 1.5,
            color: (themeVal) => themeVal.palette.mode === 'dark' ? '#e8eaed' : '#212529',
          },
          '& h4': { 
            fontSize: '1rem', 
            fontWeight: 600, 
            my: 1.5,
            color: (themeVal) => themeVal.palette.mode === 'dark' ? '#e8eaed' : '#212529',
          },
          '& h5, & h6': { 
            fontSize: '0.9rem', 
            fontWeight: 600, 
            my: 1.5,
            color: (themeVal) => themeVal.palette.mode === 'dark' ? '#e8eaed' : '#212529',
          },
          '& ul, & ol': { pl: 2.5, mb: 1.5, mt: 0 },
          '& li': { mb: 0.75 },
          '& blockquote': {
            pl: 1.5,
            ml: 0,
            borderLeft: (themeVal) => `4px solid ${themeVal.palette.primary.main}`,
            color: (themeVal) => themeVal.palette.mode === 'dark' ? '#b8bcc8' : 'text.secondary',
            fontStyle: 'italic',
            my: 1.5,
            backgroundColor: (themeVal) => themeVal.palette.mode === 'dark' 
              ? 'rgba(74, 158, 255, 0.05)'
              : 'rgba(0, 102, 204, 0.02)',
            py: 1,
            borderRadius: '0 4px 4px 0',
          },
          '& code': {
            backgroundColor: (themeVal) => themeVal.palette.mode === 'dark' 
              ? '#404448' 
              : 'rgba(0, 0, 0, 0.04)',
            color: (themeVal) => themeVal.palette.mode === 'dark' ? '#e8eaed' : '#212529',
            padding: '0.2em 0.4em',
            borderRadius: '4px',
            fontFamily: '"Consolas", "Monaco", "Courier New", monospace',
            fontSize: '0.85em',
            border: (themeVal) => themeVal.palette.mode === 'dark' 
              ? '1px solid #484b52' 
              : '1px solid rgba(0, 0, 0, 0.08)',
          },
          '& pre': {
            backgroundColor: (themeVal) => themeVal.palette.mode === 'dark' 
              ? '#1e2125' 
              : 'rgba(0, 0, 0, 0.02)',
            border: (themeVal) => themeVal.palette.mode === 'dark' 
              ? '1px solid #404448' 
              : '1px solid rgba(0, 0, 0, 0.08)',
            padding: 2,
            borderRadius: '6px',
            overflow: 'auto',
            my: 1.5,
            '& code': {
              backgroundColor: 'transparent',
              border: 'none',
              padding: 0,
              borderRadius: 0,
              fontFamily: '"Consolas", "Monaco", "Courier New", monospace',
              fontSize: '0.85em',
            },
          },
          '& a': {
            color: (themeVal) => themeVal.palette.mode === 'dark' 
              ? themeVal.palette.primary.light 
              : themeVal.palette.primary.main,
            textDecoration: 'none',
            borderBottom: '1px dotted',
            borderColor: (themeVal) => themeVal.palette.mode === 'dark' 
              ? themeVal.palette.primary.light 
              : themeVal.palette.primary.light,
            transition: 'all 0.2s ease',
            '&:hover': {
              color: (themeVal) => themeVal.palette.mode === 'dark' 
                ? themeVal.palette.primary.main 
                : themeVal.palette.primary.dark,
              borderColor: (themeVal) => themeVal.palette.primary.main,
            },
          },
          '& img': {
            maxWidth: '100%',
            borderRadius: '6px',
            border: (themeVal) => themeVal.palette.mode === 'dark' 
              ? '1px solid #404448' 
              : '1px solid #e1e5e9',
          },
          '& table': {
            borderCollapse: 'collapse',
            width: '100%',
            mb: 1.5,
            border: (themeVal) => themeVal.palette.mode === 'dark' 
              ? '1px solid #404448' 
              : '1px solid #e1e5e9',
            borderRadius: '4px',
            overflow: 'hidden',
          },
          '& th, & td': {
            border: (themeVal) => themeVal.palette.mode === 'dark' 
              ? '1px solid #404448' 
              : '1px solid #e1e5e9',
            padding: '8px 12px',
            textAlign: 'left',
            fontSize: '12px',
          },
          '& th': {
            backgroundColor: (themeVal) => themeVal.palette.mode === 'dark' 
              ? '#3a3d42' 
              : '#e9ecef',
            fontWeight: 600,
            color: (themeVal) => themeVal.palette.mode === 'dark' ? '#e8eaed' : '#495057',
          },
          '& td': {
            backgroundColor: (themeVal) => themeVal.palette.mode === 'dark' 
              ? '#2a2d32' 
              : '#ffffff',
          },
        }}
      >
        <ReactMarkdown
        // If you've added remark-gfm:
        // remarkPlugins={[remarkGfm]}
        >
          {message.content}
        </ReactMarkdown>
      </Box>
    )}
    
    {/* Citations Section */}
    {message.citations && message.citations?.length > 0 && (
      <Box sx={{ mt: 2 }}>
        <Tooltip title={isExpanded ? 'Hide Citations' : 'Show Citations'}>
          <Button
            variant="text"
            size="small"
            onClick={() => onToggleCitations(index)}
            startIcon={
              <Icon icon={isExpanded ? downIcon : rightIcon} width={14} height={14} />
            }
            sx={{
              color: (themeVal) => themeVal.palette.mode === 'dark' 
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
              borderColor: (themeVal) => themeVal.palette.mode === 'dark'
                ? alpha(themeVal.palette.primary.main, 0.2)
                : alpha(themeVal.palette.primary.main, 0.15),
              backgroundColor: (themeVal) => themeVal.palette.mode === 'dark'
                ? alpha(themeVal.palette.primary.main, 0.08)
                : alpha(themeVal.palette.primary.main, 0.04),
              '&:hover': {
                backgroundColor: (themeVal) => themeVal.palette.mode === 'dark'
                  ? alpha(themeVal.palette.primary.main, 0.12)
                  : alpha(themeVal.palette.primary.main, 0.06),
                borderColor: (themeVal) => themeVal.palette.mode === 'dark'
                  ? alpha(themeVal.palette.primary.main, 0.3)
                  : alpha(themeVal.palette.primary.main, 0.2),
              },
            }}
          >
            {message.citations.length} {message.citations.length === 1 ? 'Source' : 'Sources'}
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
                  bgcolor: (themeVal) => themeVal.palette.mode === 'dark' ? '#2a2d32' : '#f8f9fa',
                  borderRadius: '6px',
                  border: '1px solid',
                  borderColor: (themeVal) => themeVal.palette.mode === 'dark' ? '#404448' : '#e1e5e9',
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
                      color: (themeVal) => themeVal.palette.mode === 'dark' ? '#e8eaed' : '#495057',
                      fontStyle: 'normal',
                      fontWeight: 400,
                      mb: 2,
                      fontFamily: '"Segoe UI", Tahoma, Geneva, Verdana, sans-serif',
                    }}
                  >
                    {citation.content}
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
                          sx={{
                            textTransform: 'none',
                            fontSize: '11px',
                            fontWeight: 500,
                            fontFamily: '"Segoe UI", Tahoma, Geneva, Verdana, sans-serif',
                            color: (themeVal) => themeVal.palette.mode === 'dark'
                              ? themeVal.palette.primary.light
                              : themeVal.palette.primary.main,
                            py: 0.75,
                            px: 2,
                            minWidth: 0,
                            borderRadius: '4px',
                            border: '1px solid',
                            borderColor: (themeVal) => themeVal.palette.mode === 'dark'
                              ? alpha(themeVal.palette.primary.main, 0.3)
                              : alpha(themeVal.palette.primary.main, 0.2),
                            backgroundColor: (themeVal) => themeVal.palette.mode === 'dark'
                              ? alpha(themeVal.palette.primary.main, 0.1)
                              : alpha(themeVal.palette.primary.main, 0.05),
                            '&:hover': {
                              backgroundColor: (themeVal) => themeVal.palette.mode === 'dark'
                                ? alpha(themeVal.palette.primary.main, 0.15)
                                : alpha(themeVal.palette.primary.main, 0.08),
                              borderColor: (themeVal) => themeVal.palette.mode === 'dark'
                                ? alpha(themeVal.palette.primary.main, 0.4)
                                : alpha(themeVal.palette.primary.main, 0.3),
                            },
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
                            const record: Record = {
                              ...citation.metadata,
                              citations: [],
                            };
                            handleOpenRecordDetails(record);
                          }
                        }}
                        sx={{
                          textTransform: 'none',
                          fontSize: '11px',
                          fontWeight: 500,
                          fontFamily: '"Segoe UI", Tahoma, Geneva, Verdana, sans-serif',
                          color: (themeVal) => themeVal.palette.mode === 'dark'
                            ? themeVal.palette.text.secondary
                            : themeVal.palette.text.secondary,
                          py: 0.75,
                          px: 2,
                          minWidth: 0,
                          borderRadius: '4px',
                          border: '1px solid',
                          borderColor: (themeVal) => themeVal.palette.mode === 'dark'
                            ? alpha(themeVal.palette.divider, 0.3)
                            : alpha(themeVal.palette.divider, 0.5),
                          backgroundColor: (themeVal) => themeVal.palette.mode === 'dark'
                            ? alpha(themeVal.palette.background.paper, 0.5)
                            : alpha(themeVal.palette.action.hover, 0.3),
                          '&:hover': {
                            backgroundColor: (themeVal) => themeVal.palette.mode === 'dark'
                              ? alpha(themeVal.palette.background.paper, 0.7)
                              : alpha(themeVal.palette.action.hover, 0.5),
                            borderColor: (themeVal) => themeVal.palette.mode === 'dark'
                              ? alpha(themeVal.palette.divider, 0.5)
                              : alpha(themeVal.palette.divider, 0.7),
                          },
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
              onClick={() => onToggleCitations(index)}
              startIcon={
                <Icon icon={upIcon} width={14} height={14} />
              }
              sx={{
                color: (themeVal) => themeVal.palette.mode === 'dark' 
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
                borderColor: (themeVal) => themeVal.palette.mode === 'dark'
                  ? alpha(themeVal.palette.primary.main, 0.2)
                  : alpha(theme.palette.primary.main, 0.15),
                backgroundColor: (themeVal) => themeVal.palette.mode === 'dark'
                  ? alpha(themeVal.palette.primary.main, 0.08)
                  : alpha(themeVal.palette.primary.main, 0.04),
                '&:hover': {
                  backgroundColor: (themeVal) => themeVal.palette.mode === 'dark'
                    ? alpha(themeVal.palette.primary.main, 0.12)
                    : alpha(themeVal.palette.primary.main, 0.06),
                  borderColor: (themeVal) => themeVal.palette.mode === 'dark'
                    ? alpha(themeVal.palette.primary.main, 0.3)
                    : alpha(themeVal.palette.primary.main, 0.2),
                },
              }}
            >
              Hide citations
            </Button>
          </Tooltip>
        )}
      </Box>
    )}
    
    {/* Message Controls */}
    {message.type === 'bot' && (
      <>
        <Divider 
          sx={{ 
            my: 1,
            borderColor: (themeVal) => themeVal.palette.mode === 'dark' ? '#404448' : '#e1e5e9',
          }} 
        />
        <Stack direction="row" spacing={1} alignItems="center">
          {showRegenerate && (
            <>
              <Tooltip title="Regenerate response">
                <IconButton
                  onClick={() => onRegenerate(message.id)}
                  size="small"
                  disabled={isRegenerating}
                  sx={{
                    color: (themeVal) => themeVal.palette.mode === 'dark' ? '#b8bcc8' : 'text.secondary',
                    '&:hover': {
                      color: (themeVal) => themeVal.palette.mode === 'dark' 
                        ? themeVal.palette.primary.light 
                        : themeVal.palette.primary.main,
                      backgroundColor: (themeVal) => theme.palette.mode === 'dark'
                        ? alpha(themeVal.palette.primary.main, 0.1)
                        : alpha(themeVal.palette.primary.main, 0.05),
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

      {/* Record Details Dialog */}
      <Dialog
        open={isRecordDialogOpen}
        onClose={handleCloseRecordDetails}
        maxWidth="md"
        fullWidth
        BackdropProps={{
          sx: {
            backdropFilter: 'blur(1px)',
            backgroundColor: alpha(theme.palette.common.black, 0.3),
          },
        }}
        PaperProps={{
          elevation: 1,
          sx: {
            borderRadius: '12px',
            boxShadow: '0 4px 20px rgba(0, 0, 0, 0.04)',
          },
        }}
      >
        <DialogTitle
          sx={{
            fontSize: '1rem',
            fontWeight: 500,
            py: 2,
            px: 2.5,
            borderBottom: '1px solid',
            borderColor: 'divider',
          }}
        >
          Record Details
        </DialogTitle>
        <DialogContent
          sx={{
            p: 2.5,
            // ...scrollableContainerStyle
          }}
        >
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
              left: message.type === 'user' ? 'auto' : '50%',
              right: message.type === 'user' ? '50%' : 'auto',
              transform: 'translate(-50%, -50%)',
              zIndex: 1,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: 1,
            }}
          >
            <CircularProgress size={24} />
            <Typography
              variant="caption"
              sx={{
                color: 'text.secondary',
                fontWeight: 500,
                bgcolor: 'background.paper',
                px: 2,
                py: 0.5,
                borderRadius: 1,
                boxShadow: '0 2px 12px rgba(0, 0, 0, 0.03)',
              }}
            >
              Regenerating...
            </Typography>
          </Box>
        </Fade>
      )}
    </Box>
  );
};

export default ChatMessage;
