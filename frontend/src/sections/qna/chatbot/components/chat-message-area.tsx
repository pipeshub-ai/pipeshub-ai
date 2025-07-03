// src/components/chat/chat-message-area.tsx

import type { CustomCitation, FormattedMessage } from 'src/types/chat-bot';

import React, { useMemo, useState, useEffect, useCallback } from 'react';
import { Box, Fade, Stack, Typography, useTheme, alpha } from '@mui/material';
import { createScrollableContainerStyle } from '../utils/styles/scrollbar';

import ChatMessage from './chat-message';

type ChatMessagesAreaProps = {
  messages: FormattedMessage[];
  isLoading: boolean;
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
  // FIX: Add isStatusVisible to props
  currentStatus?: string;
  isStatusVisible?: boolean;
};

type ProcessingIndicatorProps = {
  displayText: string;
};

type MessageWithControlsProps = {
  message: FormattedMessage;
  index: number;
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

// FIX: Simplified the ProcessingIndicator to just display text. The logic to show/hide it is in the parent.
const ProcessingIndicator = React.memo(({ displayText }: ProcessingIndicatorProps) => {
  const theme = useTheme();

  const renderAnimation = () => {
    // ... (This function is unchanged)
    if (!displayText) return 'thinking';
    if (displayText.includes('🔍') || displayText.toLowerCase().includes('search')) {
      return 'searching';
    }
    return 'processing';
  };

  const getAnimationType = () => {
    // ... (This function is unchanged)
    if (!displayText) return 'thinking';
    if (displayText.includes('🔍') || displayText.toLowerCase().includes('search')) {
      return 'searching';
    }
    return 'processing';
  };

  return (
    <Fade in timeout={200}>
      <Box sx={{ display: 'flex', justifyContent: 'flex-start', mb: 2 }}>
        <Stack
          direction="row"
          spacing={1.5}
          alignItems="center"
          sx={{
            py: 1,
            px: 2,
            borderRadius: '12px',
            bgcolor:
              theme.palette.mode === 'dark'
                ? alpha(theme.palette.background.paper, 0.7)
                : alpha(theme.palette.background.default, 0.9),
            backdropFilter: 'blur(4px)',
            boxShadow: theme.shadows[2],
            border: '1px solid',
            borderColor: theme.palette.divider,
          }}
        >
          <Box sx={{ minWidth: 20, height: 20, display: 'flex', alignItems: 'center' }}>
            {/* This is a simplified animation part */}
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
              {[0, 1, 2].map((i) => (
                <Box
                  key={i}
                  sx={{
                    width: 5,
                    height: 5,
                    borderRadius: '50%',
                    bgcolor: 'text.secondary',
                    animation: `bounce 1.4s ease-in-out ${i * 0.16}s infinite`,
                    '@keyframes bounce': {
                      '0%, 80%, 100%': { transform: 'scale(0.8)', opacity: 0.5 },
                      '40%': { transform: 'scale(1)', opacity: 1 },
                    },
                  }}
                />
              ))}
            </Box>
          </Box>
          <Typography
            variant="body2"
            sx={{ fontSize: '0.8rem', fontWeight: 500, color: 'text.secondary' }}
          >
            {displayText}
          </Typography>
        </Stack>
      </Box>
    </Fade>
  );
});

const ChatMessagesArea = ({
  messages,
  isLoading,
  onRegenerateMessage,
  onFeedbackSubmit,
  conversationId,
  isLoadingConversation,
  onViewPdf,
  currentStatus,
  isStatusVisible, // FIX: Destructure the new prop
}: ChatMessagesAreaProps) => {
  const messagesEndRef = React.useRef<HTMLDivElement | null>(null);
  const messagesContainerRef = React.useRef<HTMLDivElement | null>(null);
  const [shouldAutoScroll, setShouldAutoScroll] = useState(true);
  const prevMessagesLength = React.useRef(messages.length);
  const scrollTimeoutRef = React.useRef<NodeJS.Timeout | null>(null);

  const displayMessages = useMemo(() => messages, [messages]);

  const hasStreamingContent = useMemo(() => {
    // ... (This function is unchanged)
    if (displayMessages.length === 0) return false;
    const lastMessage = displayMessages[displayMessages.length - 1];
    return lastMessage?.type === 'bot' && lastMessage?.id?.startsWith('streaming-');
  }, [displayMessages]);

  const canRegenerateMessage = useCallback(
    // ... (This function is unchanged)
    (message: FormattedMessage) => {
      const botMessages = messages.filter((msg) => msg.type === 'bot');
      const lastBotMessage = botMessages[botMessages.length - 1];
      return (
        message.type === 'bot' &&
        message.id === lastBotMessage?.id &&
        !message.id.startsWith('streaming-')
      );
    },
    [messages]
  );

  // ... (scrolling functions are unchanged)
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
    const isNearBottom = scrollHeight - scrollTop - clientHeight < 150;
    setShouldAutoScroll(isNearBottom);
  }, []);

  // ... (useEffect hooks are unchanged)
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
      return () => {
        if (scrollTimeoutRef.current) clearTimeout(scrollTimeoutRef.current);
      };
    }
    return undefined; // Explicit return for consistency
  }, [hasStreamingContent, shouldAutoScroll, scrollToBottomSmooth]);

  useEffect(() => {
    if (conversationId) {
      setShouldAutoScroll(true);
      if (displayMessages.length > 0) setTimeout(scrollToBottomImmediate, 100);
    }
  }, [conversationId, displayMessages.length, scrollToBottomImmediate]);

  // FIX: Updated logic to determine when to show the status indicator.
  const shouldShowLoadingIndicator = useMemo(() => {
    if (hasStreamingContent) return false; // Don't show if the final answer is already streaming
    if (isLoadingConversation && messages.length === 0) return true; // Show when loading a whole conversation
    if (isStatusVisible && currentStatus) return true; // Show if the parent says so
    return false;
  }, [isLoadingConversation, messages.length, currentStatus, isStatusVisible, hasStreamingContent]);

  // FIX: Determine the text to display in the indicator
  const indicatorText = useMemo(() => {
    if (isLoadingConversation && messages.length === 0) return 'Loading conversation...';
    return currentStatus || '';
  }, [isLoadingConversation, messages.length, currentStatus]);

  const theme = useTheme();
  const scrollableStyles = createScrollableContainerStyle(theme);

  useEffect(
    () => () => {
      if (scrollTimeoutRef.current) clearTimeout(scrollTimeoutRef.current);
    },
    []
  );

  return (
    <Box
      ref={messagesContainerRef}
      onScroll={handleScroll}
      sx={{
        flexGrow: 1,
        overflow: 'auto',
        p: 3,
        display: 'flex',
        flexDirection: 'column',
        minHeight: 0,
        ...scrollableStyles,
      }}
    >
      <Box
        sx={{
          flexGrow: 1,
          display: 'flex',
          flexDirection: 'column',
          height: '100%',
          position: 'relative',
        }}
      >
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
        {/* FIX: Render the indicator in the flow with the correct text */}
        {shouldShowLoadingIndicator && (
          <Box sx={{ mt: 1,mb:4 }}>
            <ProcessingIndicator displayText={indicatorText} />
          </Box>
        )}
        <Box sx={{ minHeight: 20 }} />
        <div
          ref={messagesEndRef}
          style={{ float: 'left', clear: 'both', height: 1, width: '100%' }}
        />
      </Box>
    </Box>
  );
};

const MessageWithControls = React.memo(
  // ... (This component is unchanged)
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
