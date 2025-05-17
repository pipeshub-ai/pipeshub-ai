import React, { useState, useRef, useCallback, useEffect } from 'react';
import { Icon } from '@iconify/react';
import githubIcon from '@iconify-icons/mdi/github';
import sendIcon from '@iconify-icons/mdi/send';

import {
  Box,
  Paper,
  useTheme,
  Typography,
  Link,
  IconButton,
  Container,
  alpha,
} from '@mui/material';

interface WelcomeMessageProps {
  inputValue?: string;
  onInputChange: (
    value: string | React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>
  ) => void;
  onSubmit: () => Promise<void>;
  isLoading?: boolean;
}

const WelcomeMessageComponent = ({
  inputValue = '',
  onInputChange,
  onSubmit,
  isLoading = false,
}: WelcomeMessageProps) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const isSubmittingRef = useRef(false);

  const [hasText, setHasText] = useState(() => Boolean(inputValue.trim()));

  const syncWithParent = useCallback(() => {
    if (inputRef.current) {
      onInputChange(inputRef.current.value);
    }
  }, [onInputChange]);

  // Auto-resize textarea based on content
  const autoResizeTextarea = useCallback(() => {
    if (inputRef.current) {
      // Reset height first to get accurate scrollHeight
      inputRef.current.style.height = 'auto';

      // Calculate new height based on content (with limits)
      const newHeight = Math.min(Math.max(inputRef.current.scrollHeight, 50), 200);
      inputRef.current.style.height = `${newHeight}px`;
    }
  }, []);

  // Handle changes directly in the component
  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      // Update local state for button enabling/disabling
      setHasText(!!e.target.value.trim());

      // Only update parent state when needed - this reduces flickering
      onInputChange(e.target.value);

      // Resize the textarea
      autoResizeTextarea();
    },
    [onInputChange, autoResizeTextarea]
  );

  // Improved submit function
  const submitMessage = useCallback(async () => {
    if (
      !inputRef.current ||
      !inputRef.current.value.trim() ||
      isLoading ||
      isSubmittingRef.current
    ) {
      return;
    }

    // Save message content before clearing
    const messageContent = inputRef.current.value;

    // Mark as submitting to prevent multiple submissions
    isSubmittingRef.current = true;

    try {
      // Update parent state with the message
      onInputChange(messageContent);

      // Clear input for better UX
      if (inputRef.current) {
        inputRef.current.value = '';
        setHasText(false);
      }

      // Submit the message after UI updates
      await onSubmit();

      // Let parent know the input is now empty
      onInputChange('');
    } finally {
      isSubmittingRef.current = false;
    }
  }, [isLoading, onSubmit, onInputChange]);

  // Handle enter key press for submission
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        submitMessage();
      }
    },
    [submitMessage]
  );

  // Setup on mount - focus, set initial value, add scrollbar style
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (inputRef.current) {
      // Set initial value and state
      if (inputValue) {
        inputRef.current.value = inputValue;
        setHasText(!!inputValue.trim());
      }

      // Focus the textarea
      inputRef.current.focus();

      // Initial resize
      autoResizeTextarea();

      // Add custom scrollbar styles
      const styleId = 'welcome-textarea-style';
      if (!document.getElementById(styleId)) {
        const style = document.createElement('style');
        style.id = styleId;
        style.textContent = isDark
          ? `
          textarea::-webkit-scrollbar {
            width: 6px;
            background-color: transparent;
          }
          textarea::-webkit-scrollbar-thumb {
            background-color: rgba(255, 255, 255, 0.2);
            border-radius: 10px;
          }
          textarea::-webkit-scrollbar-thumb:hover {
            background-color: rgba(255, 255, 255, 0.3);
          }
        `
          : `
          textarea::-webkit-scrollbar {
            width: 6px;
            background-color: transparent;
          }
          textarea::-webkit-scrollbar-thumb {
            background-color: rgba(0, 0, 0, 0.2);
            border-radius: 10px;
          }
          textarea::-webkit-scrollbar-thumb:hover {
            background-color: rgba(0, 0, 0, 0.3);
          }
        `;
        document.head.appendChild(style);
      }

      // Cleanup function
      return () => {
        const styleElement = document.getElementById(styleId);
        if (styleElement) {
          document.head.removeChild(styleElement);
        }
      };
    }
        return undefined;

    // Only depend on isDark, not on inputValue to prevent re-runs
  }, [isDark, autoResizeTextarea,inputValue]);

  return (
    <Container
      sx={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100%',
        maxWidth: '960px',
        padding: { xs: '16px', sm: '24px' },
        position: 'relative',
      }}
    >
      <Box
        sx={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          textAlign: 'center',
          mb: 8,
          mt: { xs: -2, sm: -8 },
        }}
      >
        <Typography
          variant="h5"
          sx={{
            fontWeight: 600,
            color: theme.palette.text.primary,
            mb: 2,
            fontSize: { xs: '1.6rem', sm: '1.85rem' },
            letterSpacing: '-0.03em',
            background: isDark
              ? 'linear-gradient(90deg, #fff 0%, #e0e0e0 100%)'
              : 'linear-gradient(90deg, #222 0%, #555 100%)',
            WebkitBackgroundClip: 'text',
            WebkitTextFillColor: 'transparent',
          }}
        >
          PipesHub AI
        </Typography>

        <Typography
          variant="caption"
          sx={{
            fontWeight: 500,
            color: theme.palette.primary.main,
            letterSpacing: '0.02em',
            fontSize: '0.85rem',
            opacity: 0.92,
            background: isDark
              ? `linear-gradient(90deg, ${theme.palette.primary.main} 0%, ${alpha(theme.palette.primary.light, 0.8)} 100%)`
              : `linear-gradient(90deg, ${theme.palette.primary.main} 0%, ${theme.palette.primary.dark} 100%)`,
            WebkitBackgroundClip: 'text',
            WebkitTextFillColor: 'transparent',
          }}
        >
          Workplace AI that understands your workplace inside out
        </Typography>

      </Box>

      {/* Chat Input - Modern & Minimal Style */}
      <Box sx={{ width: { xs: '95%', sm: '80%', md: '70%', lg: '60%' } }}>
        <Paper
          elevation={0}
          sx={{
            display: 'flex',
            alignItems: 'flex-end',
            p: '9px 14px',
            borderRadius: '10px',
            backgroundColor: isDark ? alpha('#131417', 0.5) : alpha('#f8f9fa', 0.6),
            border: '1px solid',
            borderColor: isDark ? alpha('#fff', 0.06) : alpha('#000', 0.04),
            boxShadow: isDark ? '0 4px 16px rgba(0, 0, 0, 0.2)' : '0 2px 10px rgba(0, 0, 0, 0.03)',
            transition: 'all 0.25s cubic-bezier(0.4, 0, 0.2, 1)',
            '&:hover': {
              borderColor: isDark ? alpha('#fff', 0.1) : alpha('#000', 0.07),
              boxShadow: isDark
                ? '0 6px 20px rgba(0, 0, 0, 0.25)'
                : '0 4px 14px rgba(0, 0, 0, 0.05)',
              backgroundColor: isDark ? alpha('#131417', 0.7) : alpha('#fff', 0.9),
            },
          }}
        >
          <textarea
            ref={inputRef}
            placeholder="Ask anything..."
            onChange={handleChange}
            onKeyDown={handleKeyDown}
            defaultValue=""
            style={{
              width: '100%',
              border: 'none',
              outline: 'none',
              background: 'transparent',
              color: isDark ? alpha('#fff', 0.95).toString() : alpha('#000', 0.85).toString(),
              fontSize: '0.9rem',
              lineHeight: 1.5,
              minHeight: '46px',
              maxHeight: '180px',
              resize: 'none',
              padding: '8px 8px',
              fontFamily: 'Inter, system-ui, -apple-system, sans-serif',
              margin: '0 6px 0 0',
              overflowY: 'auto',
              overflowX: 'hidden',
              transition: 'all 0.15s ease',
              letterSpacing: '0.01em',
            }}
          />

          <IconButton
            size="medium"
            onClick={submitMessage}
            disabled={isLoading || !hasText}
            sx={{
              backgroundColor:
                !isLoading && hasText ? alpha(theme.palette.primary.main, 0.9) : 'transparent',
              width: 34,
              height: 34,
              borderRadius: '8px',
              mb: 0.5,
              mr: -0.5,
              transition: 'all 0.2s cubic-bezier(0.4, 0, 0.2, 1)',
              color:
                !isLoading && hasText ? '#fff' : isDark ? alpha('#fff', 0.4) : alpha('#000', 0.3),
              opacity: !isLoading && hasText ? 1 : 0.6,
              border:
                !isLoading && hasText
                  ? 'none'
                  : `1px solid ${isDark ? alpha('#fff', 0.1) : alpha('#000', 0.05)}`,
              '&:hover': {
                backgroundColor:
                  !isLoading && hasText
                    ? theme.palette.primary.main
                    : isDark
                      ? alpha('#fff', 0.05)
                      : alpha('#000', 0.04),
                transform: !isLoading && hasText ? 'translateY(-1px)' : 'none',
                boxShadow: !isLoading && hasText ? '0 4px 8px rgba(0, 0, 0, 0.15)' : 'none',
              },
              '&:active': {
                transform: !isLoading && hasText ? 'translateY(0)' : 'none',
                boxShadow: 'none',
              },
              '&.Mui-disabled': {
                opacity: 0.4,
                backgroundColor: 'transparent',
                border: `1px solid ${isDark ? alpha('#fff', 0.05) : alpha('#000', 0.03)}`,
              },
            }}
          >
            <Icon
              icon={sendIcon}
              style={{
                fontSize: '1rem',
                transform: 'translateX(1px)',
                filter: !isLoading && hasText ? 'drop-shadow(0 1px 1px rgba(0,0,0,0.1))' : 'none',
              }}
            />
          </IconButton>
        </Paper>
      </Box>

      {/* Footer - More minimal */}
      <Box
        sx={{
          display: 'flex',
          justifyContent: 'center',
          mt: 'auto',
          pt: 2,
          pb: 3,
          width: '100%',
          marginTop: 6,
        }}
      >
        <Link
          href="https://github.com/pipeshub-ai/pipeshub-ai"
          target="_blank"
          underline="none"
          sx={{
            display: 'flex',
            alignItems: 'center',
            gap: 0.75,
            fontSize: '0.7rem',
            color: isDark ? alpha('#fff', 0.5) : alpha('#000', 0.4),
            opacity: 0.85,
            transition: 'all 0.25s cubic-bezier(0.4, 0, 0.2, 1)',
            padding: '6px 12px',
            borderRadius: '6px',
            '&:hover': {
              color: theme.palette.primary.main,
              opacity: 1,
              backgroundColor: isDark ? alpha('#fff', 0.03) : alpha('#000', 0.02),
              transform: 'translateY(-1px)',
            },
          }}
        >
          <Icon
            icon={githubIcon}
            style={{
              fontSize: '0.9rem',
              color: 'inherit',
            }}
          />
          pipeshub-ai
        </Link>
      </Box>
    </Container>
  );
};

const WelcomeMessage = React.memo(WelcomeMessageComponent);

WelcomeMessage.displayName = 'WelcomeMessage';

export default WelcomeMessage;
