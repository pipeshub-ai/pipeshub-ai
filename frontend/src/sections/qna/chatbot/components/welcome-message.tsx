// WelcomeMessage.tsx - With separated query state
import React, { useState, useRef, useCallback, useEffect, memo } from 'react';
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

// Separate TextInput component that manages its own state
const TextInput = memo(
  ({ onSubmit, isLoading }: { onSubmit: (text: string) => void; isLoading: boolean }) => {
    const theme = useTheme();
    const isDark = theme.palette.mode === 'dark';
    const inputRef = useRef<HTMLTextAreaElement>(null);
    const [inputText, setInputText] = useState('');
    const [hasText, setHasText] = useState(false);
    const resizeTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const isSubmittingRef = useRef(false);

    // Auto-resize textarea with debounce
    const autoResizeTextarea = useCallback(() => {
      if (inputRef.current) {
        inputRef.current.style.height = 'auto';
        const newHeight = Math.min(Math.max(inputRef.current.scrollHeight, 50), 200);
        inputRef.current.style.height = `${newHeight}px`;
      }
    }, []);

    // Handle input changes locally
    const handleChange = useCallback(
      (e: React.ChangeEvent<HTMLTextAreaElement>) => {
        const value = e.target.value;
        setInputText(value);
        setHasText(!!value.trim());

        // Debounce resize to prevent excessive calculations
        if (resizeTimeoutRef.current) {
          clearTimeout(resizeTimeoutRef.current);
        }
        resizeTimeoutRef.current = setTimeout(autoResizeTextarea, 50);
      },
      [autoResizeTextarea]
    );

    // Handle submission
    const handleSubmit = useCallback(() => {
      if (!hasText || isLoading || isSubmittingRef.current) return;

      const text = inputText.trim();
      if (!text) return;

      // Mark as submitting to prevent double submissions
      isSubmittingRef.current = true;

      try {
        // Clear input immediately for UI feedback
        const messageCopy = text; // Make a copy for submission
        setInputText('');
        setHasText(false);

        // Pass the message copy to parent
        onSubmit(messageCopy);

        // Reset textarea height
        if (inputRef.current) {
          setTimeout(() => {
            if (inputRef.current) {
              inputRef.current.style.height = '46px';
            }
          }, 50);
        }
      } finally {
        // Reset submission state after a delay
        setTimeout(() => {
          isSubmittingRef.current = false;
        }, 300);
      }
    }, [inputText, hasText, isLoading, onSubmit]);

    // Handle Enter key press
    const handleKeyDown = useCallback(
      (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          handleSubmit();
        }
      },
      [handleSubmit]
    );

    // Setup on mount
    useEffect(() => {
      if (inputRef.current) {
        // Focus the textarea
        inputRef.current.focus();

        // Add scrollbar styles
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

        // Cleanup
        return () => {
          if (resizeTimeoutRef.current) {
            clearTimeout(resizeTimeoutRef.current);
          }
          const styleElement = document.getElementById(styleId);
          if (styleElement) {
            document.head.removeChild(styleElement);
          }
        };
      }
      return undefined;
    }, [isDark]);

    return (
      <Paper
        elevation={0}
        sx={{
          display: 'flex',
          alignItems: 'center',
          p: '9px 14px',
          borderRadius: '10px',
          backgroundColor: isDark ? alpha('#131417', 0.5) : alpha('#f8f9fa', 0.6),
          border: '1px solid',
          borderColor: isDark ? alpha('#fff', 0.06) : alpha('#000', 0.04),
          boxShadow: isDark ? '0 4px 16px rgba(0, 0, 0, 0.2)' : '0 2px 10px rgba(0, 0, 0, 0.03)',
          transition: 'all 0.25s cubic-bezier(0.4, 0, 0.2, 1)',
          '&:hover': {
            borderColor: isDark ? alpha('#fff', 0.1) : alpha('#000', 0.07),
            boxShadow: isDark ? '0 6px 20px rgba(0, 0, 0, 0.25)' : '0 4px 14px rgba(0, 0, 0, 0.05)',
            backgroundColor: isDark ? alpha('#131417', 0.7) : alpha('#fff', 0.9),
          },
        }}
      >
        <textarea
          ref={inputRef}
          placeholder="Ask anything..."
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          value={inputText}
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
            transition: 'height 0.15s ease',
            letterSpacing: '0.01em',
          }}
        />

        <IconButton
          size="medium"
          onClick={handleSubmit}
          disabled={isLoading || !hasText}
          sx={{
            backgroundColor:
              !isLoading && hasText ? alpha(theme.palette.primary.main, 0.9) : 'transparent',
            width: 34,
            height: 34,
            borderRadius: '8px',
            flexShrink: 0,
            alignSelf: 'center',
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
    );
  }
);

// Simple Footer component
const Footer = memo(({ isDark }: { isDark: boolean }) => {
  const theme = useTheme();

  return (
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
  );
});

TextInput.displayName = 'TextInput';
Footer.displayName = 'Footer';

interface WelcomeMessageProps {
  onSubmit: (message: string) => Promise<void>;
  isLoading?: boolean;
}

// Main WelcomeMessage component
const WelcomeMessageComponent = ({ onSubmit, isLoading = false }: WelcomeMessageProps) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const messageRef = useRef('');
  const isSubmittingRef = useRef(false);

  // Direct submission handler that stores message text in a ref
  const handleDirectSubmit = useCallback(
    async (text: string) => {
      if (isSubmittingRef.current) return;

      isSubmittingRef.current = true;
      try {
        await onSubmit(text); // Assuming onSubmit returns a Promise
      } catch (error) {
        console.error('Error during message submission:', error);
        // Potentially handle error display to the user here
      } finally {
        isSubmittingRef.current = false;
      }
    },
    [onSubmit]
  );

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

      {/* Text Input Component */}
      <Box sx={{ width: { xs: '95%', sm: '80%', md: '70%', lg: '60%' } }}>
        <TextInput onSubmit={handleDirectSubmit} isLoading={isLoading || isSubmittingRef.current} />
      </Box>

      {/* Footer */}
      <Footer isDark={isDark} />
    </Container>
  );
};

// Memoize the component
const WelcomeMessage = React.memo(WelcomeMessageComponent);
WelcomeMessage.displayName = 'WelcomeMessage';

export default WelcomeMessage;
