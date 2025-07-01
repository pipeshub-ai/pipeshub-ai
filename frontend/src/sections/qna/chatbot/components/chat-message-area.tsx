import type {
  Metadata,
  CustomCitation,
  FormattedMessage,
  ExpandedCitationsState,
} from 'src/types/chat-bot';

import React, { useMemo, useState, useEffect, useCallback } from 'react';
import { Box, Fade, Stack, Typography, CircularProgress, useTheme, alpha } from '@mui/material';
import { createScrollableContainerStyle } from '../utils/styles/scrollbar';

import ChatMessage from './chat-message';
// WelcomeMessage import was unused, removed for cleanliness.

type ChatMessagesAreaProps = {
  messages: FormattedMessage[];
  isLoading: boolean;
  // FIX: These props are removed as state is now managed in the child
  // expandedCitations: ExpandedCitationsState;
  // onToggleCitations: (index: number) => void;
  onRegenerateMessage: (messageId: string) => Promise<void>;
  onFeedbackSubmit: (messageId: string, feedback: any) => Promise<void>;
  conversationId: string | null;
  isLoadingConversation: boolean;
  onViewPdf: (
    url: string,
    citation: CustomCitation,
    citations: CustomCitation[],
    isExcelFile?: boolean,
    buffer?: ArrayBuffer
  ) => void;
  currentStatus?: string;
};

type ProcessingIndicatorProps = {
  isLoadingConversation: boolean;
  currentStatus?: string;
  isStreaming?: boolean;
  hasStreamingContent?: boolean;
};

type MessageWithControlsProps = {
  message: FormattedMessage;
  index: number;
  // FIX: These props are removed as state is now managed in the child
  // isExpanded: boolean;
  // onToggleCitations: (index: number) => void;
  onViewPdf: (
    url: string,
    citation: CustomCitation,
    citations: CustomCitation[],
    isExcelFile?: boolean,
    buffer?: ArrayBuffer
  ) => void;
  onFeedbackSubmit: (messageId: string, feedback: any) => Promise<void>;
  conversationId: string | null;
  onRegenerate: (messageId: string) => Promise<void>;
  showRegenerate: boolean;
};

const ProcessingIndicator = ({
  isLoadingConversation,
  currentStatus,
  hasStreamingContent,
}: ProcessingIndicatorProps) => {
  const theme = useTheme();
  const [isVisible, setIsVisible] = useState(false);
  const [displayText, setDisplayText] = useState('');

  const shouldShowIndicator = () => {
    if (hasStreamingContent) return false;
    if (isLoadingConversation) return true;
    if (
      currentStatus &&
      !currentStatus.toLowerCase().includes('complete') &&
      !currentStatus.toLowerCase().includes('finished') &&
      currentStatus.trim() !== ''
    ) {
      return true;
    }
    return false;
  };

  useEffect(() => {
    const shouldShow = shouldShowIndicator();
    if (shouldShow) {
      const newText = isLoadingConversation ? 'Loading conversation...' : currentStatus || '💭 Thinking...';
      setDisplayText(newText);
      setIsVisible(true);
    } else {
      const hideTimeout = setTimeout(() => setIsVisible(false), 100);
      return () => clearTimeout(hideTimeout);
    }
  }, [isLoadingConversation, currentStatus, hasStreamingContent]);

  const getAnimationType = () => {
    if (!currentStatus) return 'thinking';
    if (currentStatus.includes('🔍') || currentStatus.toLowerCase().includes('search')) {
      return 'searching';
    }
    return 'processing';
  };

  const renderAnimation = () => {
    const animationType = getAnimationType();
    if (animationType === 'searching') {
      return (
        <Box
          sx={{
            width: 20, height: 20, border: `2px solid ${alpha(theme.palette.primary.main, 0.3)}`,
            borderTop: `2px solid ${theme.palette.primary.main}`, borderRadius: '50%',
            animation: 'searchSpin 1s linear infinite',
            '@keyframes searchSpin': { '100%': { transform: 'rotate(360deg)' } },
          }}
        />
      );
    }
    return (
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        {[0, 1, 2].map((i) => (
          <Box
            key={i}
            sx={{
              width: 6, height: 6, borderRadius: '50%', bgcolor: theme.palette.primary.main,
              animation: `bounce 1.4s ease-in-out ${i * 0.16}s infinite`,
              '@keyframes bounce': { '0%, 80%, 100%': { transform: 'scale(0.8)', opacity: 0.5 }, '40%': { transform: 'scale(1)', opacity: 1 } },
            }}
          />
        ))}
      </Box>
    );
  };

  if (!isVisible) return null;

  return (
    <Fade in={isVisible} timeout={200}>
      <Box sx={{ display: 'flex', justifyContent: 'flex-start', mb: 2 }}>
        <Stack
          direction="row" spacing={2} alignItems="center"
          sx={{
            py: 1.5, px: 2.5, borderRadius: 1,
            bgcolor: theme.palette.mode === 'dark' ? alpha(theme.palette.background.paper, 0.6) : alpha(theme.palette.background.paper, 0.8),
            boxShadow: '0 2px 12px rgba(0, 0, 0, 0.1)',
          }}
        >
          <Box sx={{ minWidth: 20, height: 20 }}>{renderAnimation()}</Box>
          <Typography variant="body2" sx={{ fontSize: '0.8rem', fontWeight: 500, color: 'text.secondary' }}>
            {displayText}
          </Typography>
        </Stack>
      </Box>
    </Fade>
  );
};

const ChatMessagesArea = ({
  messages,
  isLoading,
  onRegenerateMessage,
  onFeedbackSubmit,
  conversationId,
  isLoadingConversation,
  onViewPdf,
  currentStatus,
}: ChatMessagesAreaProps) => {
  const messagesEndRef = React.useRef<HTMLDivElement | null>(null);
  const messagesContainerRef = React.useRef<HTMLDivElement | null>(null);
  const [shouldAutoScroll, setShouldAutoScroll] = useState(true);
  const prevMessagesLength = React.useRef(messages.length);
  const [lastMessageLength, setLastMessageLength] = useState(0);
  const lastScrollTopRef = React.useRef(0);
  const statusShownRef = React.useRef(false);
  const scrollTimeoutRef = React.useRef<NodeJS.Timeout | null>(null);
  const lastContentRef = React.useRef<string>('');
  
  const displayMessages = useMemo(() => messages, [messages]);

  const hasStreamingContent = useMemo(() => {
    if (displayMessages.length === 0) return false;
    const lastMessage = displayMessages[displayMessages.length - 1];
    const isStreamingMessage = lastMessage?.type === 'bot' && lastMessage?.id?.startsWith('streaming-');
    const contentChanged = lastMessage?.content !== lastContentRef.current;
    if (contentChanged) {
      lastContentRef.current = lastMessage?.content || '';
      setLastMessageLength(lastMessage?.content?.length || 0);
    }
    const hasGrowingContent = lastMessage?.content && lastMessage.content.length > lastMessageLength;
    return isStreamingMessage || (hasGrowingContent && contentChanged);
  }, [displayMessages, lastMessageLength]);

  const canRegenerateMessage = useCallback(
    (message: FormattedMessage) => {
      const botMessages = messages.filter((msg) => msg.type === 'bot');
      const lastBotMessage = botMessages[botMessages.length - 1];
      return message.type === 'bot' && message.id === lastBotMessage?.id && !message.id.startsWith('streaming-');
    }, [messages]
  );

  const scrollToBottomSmooth = useCallback(() => {
    if (!messagesEndRef.current || !shouldAutoScroll) return;
    if (scrollTimeoutRef.current) clearTimeout(scrollTimeoutRef.current);
    requestAnimationFrame(() => {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
    });
  }, [shouldAutoScroll]);

  const scrollToBottomImmediate = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'auto', block: 'end' });
  }, []);

  const handleScroll = useCallback(() => {
    if (!messagesContainerRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = messagesContainerRef.current;
    lastScrollTopRef.current = scrollTop;
    const isNearBottom = scrollHeight - scrollTop - clientHeight < 150;
    setShouldAutoScroll(isNearBottom);
  }, []);

  useEffect(() => {
    if (messages.length > prevMessagesLength.current) {
      const latestMessage = messages[messages.length - 1];
      if (latestMessage?.type === 'user' && shouldAutoScroll) {
        setTimeout(scrollToBottomImmediate, 50);
      }
    }
    prevMessagesLength.current = messages.length;
  }, [messages, shouldAutoScroll, scrollToBottomImmediate]);

  useEffect(() => {
    if (hasStreamingContent && shouldAutoScroll) {
      if (scrollTimeoutRef.current) clearTimeout(scrollTimeoutRef.current);
      scrollTimeoutRef.current = setTimeout(scrollToBottomSmooth, 100);
      return () => { if (scrollTimeoutRef.current) clearTimeout(scrollTimeoutRef.current); };
    }
  }, [hasStreamingContent, shouldAutoScroll, scrollToBottomSmooth]);

  useEffect(() => {
    if (conversationId) {
      setShouldAutoScroll(true);
      if (displayMessages.length > 0) setTimeout(scrollToBottomImmediate, 100);
    }
  }, [conversationId, displayMessages.length, scrollToBottomImmediate]);

  const shouldShowLoadingIndicator = useMemo(() => {
    if (hasStreamingContent) return false;
    if (isLoadingConversation && messages.length === 0) return true;
    if (currentStatus && !currentStatus.toLowerCase().includes('complete') && !hasStreamingContent) {
      return true;
    }
    return false;
  }, [isLoadingConversation, messages.length, currentStatus, hasStreamingContent]);

  const theme = useTheme();
  const scrollableStyles = createScrollableContainerStyle(theme);

  useEffect(() => () => { if (scrollTimeoutRef.current) clearTimeout(scrollTimeoutRef.current); }, []);

  return (
    <Box
      ref={messagesContainerRef}
      onScroll={handleScroll}
      sx={{
        flexGrow: 1, overflow: 'auto', p: 3, display: 'flex', flexDirection: 'column', minHeight: 0,
        ...scrollableStyles,
      }}
    >
      {isLoadingConversation && messages.length === 0 ? (
        <Box sx={{ m: 'auto' }}>
          <ProcessingIndicator isLoadingConversation={isLoadingConversation} currentStatus={currentStatus} hasStreamingContent={hasStreamingContent} />
        </Box>
      ) : (
        <Box sx={{ flexGrow: 1, display: 'flex', flexDirection: 'column', height: '100%', position: 'relative' }}>
          <Box sx={{ minHeight: 4 }} />
          {displayMessages.map((message, index) => (
            <MessageWithControls
              key={`msg-${message.id}`}
              message={message}
              index={index}
              onRegenerate={onRegenerateMessage}
              onFeedbackSubmit={onFeedbackSubmit}
              conversationId={conversationId}
              showRegenerate={canRegenerateMessage(message)}
              onViewPdf={onViewPdf}
            />
          ))}
          {shouldShowLoadingIndicator && (
            <Box sx={{ mt: 1 }}>
              <ProcessingIndicator
                isLoadingConversation={isLoadingConversation}
                currentStatus={currentStatus}
                isStreaming={isLoading && messages.length > 0}
                hasStreamingContent={hasStreamingContent}
              />
            </Box>
          )}
          <Box sx={{ minHeight: 20 }} />
          <div ref={messagesEndRef} style={{ float: 'left', clear: 'both', height: 1, width: '100%' }} />
        </Box>
      )}
    </Box>
  );
};

const MessageWithControls = React.memo(
  ({
    message,
    index,
    onRegenerate,
    onFeedbackSubmit,
    conversationId,
    showRegenerate,
    onViewPdf,
  }: MessageWithControlsProps) => {
    const [isRegenerating, setIsRegenerating] = useState(false);

    const handleRegenerate = async (messageId: string): Promise<void> => {
      setIsRegenerating(true);
      try {
        await onRegenerate(messageId);
      } finally {
        setIsRegenerating(false);
      }
    };

    return (
      <Box sx={{ mb: 2 }}>
        <ChatMessage
          message={message}
          index={index}
          // No longer passing isExpanded or onToggleCitations
          onRegenerate={handleRegenerate}
          onFeedbackSubmit={onFeedbackSubmit}
          conversationId={conversationId}
          isRegenerating={isRegenerating}
          showRegenerate={showRegenerate}
          onViewPdf={onViewPdf}
        />
      </Box>
    );
  }
);

export default ChatMessagesArea;