// components/chat-message-area.tsx
import type {
  Metadata,
  CustomCitation,
  FormattedMessage,
  ExpandedCitationsState,
} from 'src/types/chat-bot';

import React, { useMemo, useState, useEffect, useCallback, useLayoutEffect } from 'react';
import { Box, Fade, Stack, Typography, CircularProgress } from '@mui/material';

import ChatMessage from './chat-message';
import WelcomeMessage from './welcome-message';

type ChatMessagesAreaProps = {
  messages: FormattedMessage[];
  isLoading: boolean;
  expandedCitations: ExpandedCitationsState;
  onToggleCitations: (index: number) => void;
  onRegenerateMessage: (messageId: string) => Promise<void>;
  onFeedbackSubmit: (messageId: string, feedback: any) => Promise<void>;
  conversationId: string | null;
  isLoadingConversation: boolean;
  onViewPdf: (
    url: string,
    citationMeta: Metadata,
    citations: CustomCitation[],
    isExcelFile?: boolean,
    buffer?: ArrayBuffer
  ) => void;
  // New props for handling input in welcome screen
  inputValue: string;
  onInputChange: (e: string | React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => void;
  onSubmit: () => Promise<void>;
  showWelcome: boolean;
};

type ProcessingIndicatorProps = {
  isLoadingConversation: boolean;
};

type MessageWithControlsProps = {
  message: FormattedMessage;
  index: number;
  isExpanded: boolean;
  onToggleCitations: (index: number) => void;
  onViewPdf: (
    url: string,
    citationMeta: Metadata,
    citations: CustomCitation[],
    isExcelFile?: boolean,
    buffer?: ArrayBuffer
  ) => void;
  onFeedbackSubmit: (messageId: string, feedback: any) => Promise<void>;
  conversationId: string | null;
  onRegenerate: (messageId: string) => Promise<void>;
  showRegenerate: boolean;
};

// Loading states for different scenarios
const ProcessingIndicator = ({ isLoadingConversation }: ProcessingIndicatorProps) => (
  <Fade in={Boolean(true)}>
    <Stack
      direction="row"
      spacing={2}
      alignItems="center"
      sx={{
        py: 2,
        px: 3,
        borderRadius: 2,
        bgcolor: 'background.paper',
        boxShadow: '0 2px 12px rgba(0, 0, 0, 0.03)',
        border: '1px solid',
        borderColor: 'divider',
        maxWidth: '300px',
      }}
    >
      <Box sx={{ position: 'relative', width: 20, height: 20 }}>
        <CircularProgress size={20} thickness={4} sx={{ color: 'primary.main' }} />
      </Box>
      <Typography
        variant="body2"
        sx={{
          color: 'text.secondary',
          fontSize: '0.875rem',
          fontWeight: 500,
          display: 'flex',
          alignItems: 'center',
        }}
      >
        {isLoadingConversation ? 'Loading Conversation...' : 'Thinking...'}
      </Typography>
    </Stack>
  </Fade>
);

// ChatMessagesArea.tsx - updated version with specific fix

const ChatMessagesArea = ({
  messages,
  isLoading,
  expandedCitations,
  onToggleCitations,
  onRegenerateMessage,
  onFeedbackSubmit,
  conversationId,
  isLoadingConversation,
  onViewPdf,
  inputValue,
  onInputChange,
  onSubmit,
  showWelcome,
}: ChatMessagesAreaProps) => {
  const messagesEndRef = React.useRef<HTMLDivElement | null>(null);
  const messagesContainerRef = React.useRef<HTMLDivElement | null>(null);
  const [shouldAutoScroll, setShouldAutoScroll] = useState(true);
  const prevMessagesLength = React.useRef(messages.length);
  
  // Critical: Track whether we're in a 'sending message' state to prevent scroll jumps
  const isSendingMessage = React.useRef(false);
  const lastScrollTopRef = React.useRef(0);

  // Remove sorting and use messages directly to maintain order
  const displayMessages = useMemo(() => messages, [messages]);

  // Find the last bot message for regeneration
  const canRegenerateMessage = useCallback(
    (message: FormattedMessage) => {
      const botMessages = messages.filter((msg) => msg.type === 'bot');
      const lastBotMessage = botMessages[botMessages.length - 1];
      return (
        message.type === 'bot' &&
        message.messageType !== 'error' &&
        message.id === lastBotMessage?.id &&
        !message.id.startsWith('error-')
      );
    },
    [messages]
  );
  
  // NEW: Function to explicitly preserve scroll position
  const preserveScrollPosition = useCallback(() => {
    if (messagesContainerRef.current) {
      // Save current position
      lastScrollTopRef.current = messagesContainerRef.current.scrollTop;
      
      // Immediately set it back (prevents browser from resetting)
      requestAnimationFrame(() => {
        if (messagesContainerRef.current) {
          messagesContainerRef.current.scrollTop = lastScrollTopRef.current;
        }
      });
    }
  }, []);

  // FIXED: Don't let the scroll change on normal user input
  const handleScroll = useCallback((e: React.UIEvent<HTMLDivElement>) => {
    if (isSendingMessage.current) {
      // If we're sending a message, prevent any scroll position changes
      preserveScrollPosition();
      return;
    }
    
    if (!messagesContainerRef.current) return;
    
    const { scrollTop, scrollHeight, clientHeight } = messagesContainerRef.current;
    
    // Update last known position
    lastScrollTopRef.current = scrollTop;
    
    // If we're close to the bottom (within 150px), enable auto-scrolling
    const isNearBottom = scrollHeight - scrollTop - clientHeight < 150;
    setShouldAutoScroll(isNearBottom);
  }, [preserveScrollPosition]);

  // New function to immediately scroll to bottom without animations
  // This prevents the visible "jump" effect
  const immediateScrollToBottom = useCallback(() => {
    if (!messagesEndRef.current || !messagesContainerRef.current) return;
    
    // Use instant scroll (no animation) to prevent visible jumps
    messagesEndRef.current.scrollIntoView({ behavior: 'auto', block: 'end' });
    
    // Update our saved position
    if (messagesContainerRef.current) {
      lastScrollTopRef.current = messagesContainerRef.current.scrollTop;
    }
  }, []);

  // Smooth scroll for non-critical operations
  const smoothScrollToBottom = useCallback(() => {
    if (!messagesEndRef.current) return;
    
    messagesEndRef.current.scrollIntoView({ 
      behavior: 'smooth', 
      block: 'end' 
    });
  }, []);

  // CRITICAL: Execute this immediately before any render
  // to temporarily disable scroll updates
  useLayoutEffect(() => {
    // Only preserve position during active sending
    if (isSendingMessage.current) {
      preserveScrollPosition();
    }
  });

  // IMPORTANT: This setup ensures scroll position is maintained when messages array changes
  useEffect(() => {
    const isNewMessage = messages.length > prevMessagesLength.current;
    
    // CRITICAL: For new messages, we need to handle scroll properly
    if (isNewMessage) {
      const latestMessage = messages[messages.length - 1];
      const isUserMessage = latestMessage?.type === 'user';
      
      if (isUserMessage) {
        // When sending a message, we initially prevent scroll jumps
        isSendingMessage.current = true;
        
        // First, preserve position
        preserveScrollPosition();
        
        // Then, after a tiny delay (to allow render to complete), jump to bottom
        requestAnimationFrame(() => {
          immediateScrollToBottom();
          
          // Clear sending flag after we've handled it
          setTimeout(() => {
            isSendingMessage.current = false;
          }, 100);
        });
      }
      else if (shouldAutoScroll) {
        // For bot messages, use smooth scroll if user hasn't scrolled up
        requestAnimationFrame(() => {
          smoothScrollToBottom();
        });
      }
    }
    
    prevMessagesLength.current = messages.length;
  }, [messages, shouldAutoScroll, immediateScrollToBottom, smoothScrollToBottom, preserveScrollPosition]);

  // Scroll to bottom when loading completes
  useEffect(() => {
    if (!isLoading && shouldAutoScroll) {
      requestAnimationFrame(() => {
        smoothScrollToBottom();
      });
    }
  }, [isLoading, shouldAutoScroll, smoothScrollToBottom]);

  // Handle conversation changes
  useEffect(() => {
    if (conversationId) {
      setShouldAutoScroll(true);
      requestAnimationFrame(() => {
        immediateScrollToBottom();
      });
    }
  }, [conversationId, immediateScrollToBottom]);

  const shouldShowLoadingIndicator = (isLoading || isLoadingConversation) && messages.length > 0;

  // Custom hook to enforce scroll position during critical renders
  useEffect(() => {
    let timer: NodeJS.Timeout | null = null;
    
    // Function to ensure we maintain scroll during updates
    const maintainScrollPosition = () => {
      if (messagesContainerRef.current) {
        messagesContainerRef.current.scrollTop = lastScrollTopRef.current;
      }
    };
    
    // Set up interval to enforce position during rapid updates
    if (isSendingMessage.current) {
      timer = setInterval(maintainScrollPosition, 10);
    }
    
    return () => {
      if (timer) clearInterval(timer);
    };
  }, []);

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
        // Critical: Remove any automatic scroll behavior
        scrollBehavior: 'auto',
        scrollPaddingBottom: '100px',
      }}
    >
      {isLoadingConversation && conversationId && messages.length === 0 ? (
        // Centered loading indicator
        <Box
          sx={{
            position: 'absolute',
            top: '50%',
            left: '50%',
            transform: 'translate(-50%, -50%)',
            width: '100%',
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
          }}
        >
          <ProcessingIndicator isLoadingConversation={isLoadingConversation} />
        </Box>
      ) : (
        <Box
          sx={{
            flexGrow: 1,
            display: 'flex',
            flexDirection: 'column',
            justifyContent: showWelcome ? 'center' : 'flex-start',
            height: '100%',
            position: 'relative',
          }}
        >
          {showWelcome ? (
            <WelcomeMessage
              inputValue={inputValue}
              onInputChange={onInputChange}
              onSubmit={onSubmit}
              isLoading={isLoading}
            />
          ) : (
            <>
              {/* Force minimum height at top to prevent scroll jump */}
              <Box sx={{ minHeight: 4 }} />
              
              {displayMessages.map((message, index) => (
                <MessageWithControls
                  key={`msg-${message.id}`}
                  message={message}
                  index={index}
                  isExpanded={expandedCitations[index]}
                  onToggleCitations={() => onToggleCitations(index)}
                  onRegenerate={onRegenerateMessage}
                  onFeedbackSubmit={onFeedbackSubmit}
                  conversationId={conversationId}
                  showRegenerate={canRegenerateMessage(message)}
                  onViewPdf={onViewPdf}
                />
              ))}
              
              {shouldShowLoadingIndicator && (
                <Box sx={{ mt: 2, display: 'flex', justifyContent: 'flex-start' }}>
                  <ProcessingIndicator isLoadingConversation={false} />
                </Box>
              )}
              
              {/* Enhanced scroll anchor with proper height */}
              <Box sx={{ minHeight: 20 }} />
              <div 
                ref={messagesEndRef} 
                style={{ 
                  float: 'left', 
                  clear: 'both',
                  height: 1,
                  width: '100%',
                }} 
              />
            </>
          )}
        </Box>
      )}
    </Box>
  );
};

const MessageWithControls = React.memo(
  ({
    message,
    index,
    isExpanded,
    onToggleCitations,
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
      <Box sx={{ position: 'relative', mb: 2, width: '100%' }}>
        <ChatMessage
          message={message}
          index={index}
          isExpanded={isExpanded}
          onToggleCitations={onToggleCitations}
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
