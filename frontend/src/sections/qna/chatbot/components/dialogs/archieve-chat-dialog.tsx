import type { AlertColor } from '@mui/material';
import type { Conversation } from 'src/types/chat-bot';
import type { SnackbarState } from 'src/types/chat-sidebar';

import { Icon } from '@iconify/react';
import closeIcon from '@iconify-icons/mdi/close';
import React, { useState, useEffect } from 'react';
import archiveIcon from '@iconify-icons/mdi/archive-off-outline';
import archiveUpIcon from '@iconify-icons/mdi/archive-arrow-up-outline';

import {
  Box,
  List,
  Fade,
  Alert,
  Paper,
  alpha,
  Dialog,
  Divider,
  Tooltip,
  ListItem,
  Snackbar,
  useTheme,
  Skeleton,
  IconButton,
  Typography,
  DialogTitle,
  DialogContent,
  ListItemButton,
  CircularProgress,
} from '@mui/material';

import axiosInstance from 'src/utils/axios';

type ArchivedChatsDialogProps = {
  open: boolean;
  onClose: () => void;
  onSelectChat: (chat: Conversation) => Promise<void>;
  onUnarchive: () => Promise<void>;
};

const formatDistanceToNow = (date: string): string => {
  const now = new Date();
  const pastDate = new Date(date);
  const diff = now.getTime() - pastDate.getTime();

  const minutes = Math.floor(diff / 60000);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);

  if (days > 0) return `${days}d ago`;
  if (hours > 0) return `${hours}h ago`;
  if (minutes > 0) return `${minutes}m ago`;
  return 'Just now';
};

const EmptyState = () => {
  const theme = useTheme();

  return (
    <Fade in>
      <Box
        display="flex"
        flexDirection="column"
        alignItems="center"
        justifyContent="center"
        py={6}
        gap={2}
      >
        <Paper
          elevation={0}
          sx={{
            p: 3,
            borderRadius: 2,
            bgcolor:
              theme.palette.mode === 'dark'
                ? alpha(theme.palette.primary.dark, 0.15)
                : alpha(theme.palette.primary.light, 0.12),
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: 2,
            border: `1px solid ${
              theme.palette.mode === 'dark'
                ? alpha(theme.palette.primary.main, 0.2)
                : alpha(theme.palette.primary.main, 0.1)
            }`,
          }}
        >
          <Icon
            icon={archiveIcon}
            width={40}
            height={40}
            color={
              theme.palette.mode === 'dark'
                ? theme.palette.primary.light
                : theme.palette.primary.main
            }
          />
          <Box textAlign="center">
            <Typography variant="subtitle1" color="text.primary" gutterBottom fontWeight={500}>
              No Archived Conversations
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Archived conversations will appear here
            </Typography>
          </Box>
        </Paper>
      </Box>
    </Fade>
  );
};

const LoadingSkeleton = () => {
  const theme = useTheme();

  return (
    <Box p={2}>
      {[...Array(3)].map((_, i) => (
        <Box key={i} sx={{ mb: 2 }}>
          <Skeleton
            variant="text"
            width="60%"
            height={24}
            sx={{
              bgcolor:
                theme.palette.mode === 'dark'
                  ? alpha(theme.palette.common.white, 0.1)
                  : alpha(theme.palette.common.black, 0.07),
            }}
          />
          <Skeleton
            variant="text"
            width="40%"
            height={20}
            sx={{
              bgcolor:
                theme.palette.mode === 'dark'
                  ? alpha(theme.palette.common.white, 0.1)
                  : alpha(theme.palette.common.black, 0.07),
            }}
          />
          <Skeleton
            variant="text"
            width="80%"
            height={20}
            sx={{
              bgcolor:
                theme.palette.mode === 'dark'
                  ? alpha(theme.palette.common.white, 0.1)
                  : alpha(theme.palette.common.black, 0.07),
            }}
          />
          {i < 2 && <Divider sx={{ mt: 2, opacity: 0.7 }} />}
        </Box>
      ))}
    </Box>
  );
};

const ArchivedChatsDialog = ({
  open,
  onClose,
  onSelectChat,
  onUnarchive,
}: ArchivedChatsDialogProps) => {
  const theme = useTheme();
  const [archivedChats, setArchivedChats] = useState<Conversation[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [isUnarchiving, setIsUnarchiving] = useState<boolean>(false);
  const [unarchivingId, setUnarchivingId] = useState<string | null>(null);
  const [snackbar, setSnackbar] = useState<SnackbarState>({
    open: false,
    message: '',
    severity: 'success',
  });

  useEffect(() => {
    if (open) {
      fetchArchivedChats();
    }
  }, [open]);

  const fetchArchivedChats = async () => {
    setIsLoading(true);
    try {
      const response = await axiosInstance.get('/api/v1/conversations/show/archives', {
        params: {
          conversationSource: 'sales',
        },
      });
      setArchivedChats(response.data.conversations || []);
    } catch (error) {
      // Error handling commented out as per original code
    } finally {
      setIsLoading(false);
    }
  };

  const handleUnarchive = async (chatId: string) => {
    setIsUnarchiving(true);
    setUnarchivingId(chatId);
    try {
      await axiosInstance.patch(`/api/v1/conversations/${chatId}/unarchive`);
      setArchivedChats((prev) => prev.filter((chat) => chat._id !== chatId));
      onUnarchive?.();

      setSnackbar({
        open: true,
        message: 'Conversation unarchived successfully',
        severity: 'success',
      });
    } catch (error) {
      // Error handling commented out as per original code
    } finally {
      setIsUnarchiving(false);
      setUnarchivingId(null);
    }
  };

  return (
    <>
      <Dialog
        open={open}
        onClose={onClose}
        maxWidth="sm"
        fullWidth
        TransitionComponent={Fade}
        BackdropProps={{
          sx: {
            backdropFilter: 'blur(1px)',
            backgroundColor: alpha(theme.palette.common.black, 0.2),
          },
        }}
        PaperProps={{
          elevation: 24,
          sx: {
            borderRadius: 3,
            maxHeight: '80vh',
            bgcolor:
              theme.palette.mode === 'dark'
                ? alpha(theme.palette.background.paper, 0.95)
                : theme.palette.background.paper,
            backdropFilter: 'blur(2px)',
            boxShadow:
              theme.palette.mode === 'dark'
                ? `0 10px 40px -10px ${alpha(theme.palette.common.black, 0.5)}, 
                 0 0 0 1px ${alpha(theme.palette.primary.dark, 0.1)}, 
                 0 0 20px 0px ${alpha(theme.palette.primary.dark, 0.1)}`
                : `0 12px 28px 0 ${alpha(theme.palette.common.black, 0.2)}, 
                 0 2px 4px 0 ${alpha(theme.palette.common.black, 0.1)}, 
                 0 0 0 1px ${alpha(theme.palette.grey[200], 0.6)}`,
            transition: 'all 0.2s ease-in-out',
          },
        }}
      >
        <DialogTitle
          sx={{
            px: 3,
            py: 2.5,
            bgcolor:
              theme.palette.mode === 'dark'
                ? alpha(theme.palette.background.paper, 0.8)
                : theme.palette.background.paper,
            borderBottom: `1px solid ${
              theme.palette.mode === 'dark'
                ? alpha(theme.palette.primary.main, 0.25)
                : theme.palette.divider
            }`,
          }}
        >
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
            }}
          >
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
              <Icon
                icon={archiveIcon}
                width={24}
                height={24}
                color={
                  theme.palette.mode === 'dark'
                    ? theme.palette.primary.light
                    : theme.palette.primary.main
                }
              />
              <Typography
                variant="h6"
                sx={{
                  fontWeight: 600,
                  fontSize: '1.125rem',
                  letterSpacing: '-0.01em',
                }}
              >
                Archived Conversations
              </Typography>
            </Box>
            <IconButton
              onClick={onClose}
              size="small"
              sx={{
                color: 'text.secondary',
                p: 1,
                borderRadius: 1.5,
                transition: 'all 0.15s ease-in-out',
                '&:hover': {
                  bgcolor:
                    theme.palette.mode === 'dark'
                      ? alpha(theme.palette.primary.main, 0.15)
                      : alpha(theme.palette.primary.main, 0.08),
                  color:
                    theme.palette.mode === 'dark'
                      ? theme.palette.primary.light
                      : theme.palette.primary.main,
                },
              }}
            >
              <Icon icon={closeIcon} width={20} height={20} />
            </IconButton>
          </Box>
        </DialogTitle>

        <Divider
          sx={{
            opacity: theme.palette.mode === 'dark' ? 0.2 : 0.6,
          }}
        />

        <DialogContent
          sx={{
            p: 0,
            '&::-webkit-scrollbar': {
              width: '6px',
              height: '6px',
            },
            '&::-webkit-scrollbar-thumb': {
              backgroundColor:
                theme.palette.mode === 'dark'
                  ? alpha(theme.palette.common.white, 0.2)
                  : alpha(theme.palette.common.black, 0.2),
              borderRadius: '6px',
            },
            '&::-webkit-scrollbar-track': {
              backgroundColor: 'transparent',
            },
          }}
        >
          {isLoading ? (
            <LoadingSkeleton />
          ) : archivedChats.length === 0 ? (
            <EmptyState />
          ) : (
            <List sx={{ p: 0 }}>
              {archivedChats.map((chat) => {
                // Get the last message content
                const lastMessage =
                  chat.messages && chat.messages.length > 0
                    ? chat.messages[chat.messages.length - 1].content
                    : 'No messages';

                return (
                  <ListItem
                    key={chat._id}
                    divider
                    disablePadding
                    secondaryAction={
                      <Tooltip title="Unarchive conversation" arrow>
                        <IconButton
                          size="small"
                          onClick={(e) => {
                            e.stopPropagation();
                            handleUnarchive(chat._id);
                          }}
                          disabled={isUnarchiving}
                          sx={{
                            ml: 1,
                            mr: 1,
                            p: 0.875,
                            borderRadius: 1.5,
                            color:
                              theme.palette.mode === 'dark'
                                ? theme.palette.primary.light
                                : theme.palette.primary.main,
                            bgcolor:
                              theme.palette.mode === 'dark'
                                ? alpha(theme.palette.primary.dark, 0.2)
                                : alpha(theme.palette.primary.main, 0.08),
                            border: `1px solid ${
                              theme.palette.mode === 'dark'
                                ? alpha(theme.palette.primary.main, 0.3)
                                : alpha(theme.palette.primary.main, 0.15)
                            }`,
                            transition: 'all 0.15s ease-in-out',
                            '&:hover': {
                              bgcolor:
                                theme.palette.mode === 'dark'
                                  ? alpha(theme.palette.primary.main, 0.3)
                                  : alpha(theme.palette.primary.main, 0.15),
                            },
                          }}
                        >
                          {isUnarchiving && unarchivingId === chat._id ? (
                            <CircularProgress
                              size={20}
                              color="inherit"
                              sx={{
                                color:
                                  theme.palette.mode === 'dark'
                                    ? theme.palette.primary.light
                                    : theme.palette.primary.main,
                              }}
                            />
                          ) : (
                            <Icon icon={archiveUpIcon} width={18} height={18} />
                          )}
                        </IconButton>
                      </Tooltip>
                    }
                    sx={{
                      position: 'relative',
                      '&:after': {
                        content: '""',
                        position: 'absolute',
                        bottom: 0,
                        left: '1.25rem',
                        right: '1.25rem',
                        height: '1px',
                        bgcolor:
                          theme.palette.mode === 'dark'
                            ? alpha(theme.palette.divider, 0.2)
                            : theme.palette.divider,
                      },
                    }}
                  >
                    <ListItemButton
                      // onClick={() => onSelectChat(chat)}
                      sx={{
                        py: 2,
                        px: 3,
                        transition: 'all 0.15s ease-in-out',
                        '&:hover': {
                          bgcolor:
                            theme.palette.mode === 'dark'
                              ? alpha(theme.palette.action.hover, 0.1)
                              : theme.palette.action.hover,
                        },
                      }}
                    >
                      <Box sx={{ width: '100%', pr: 5 }}>
                        <Box
                          sx={{
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: 'center',
                            mb: 0.75,
                          }}
                        >
                          <Typography
                            variant="subtitle1"
                            sx={{
                              fontWeight: 600,
                              color: 'text.primary',
                              fontSize: '0.9375rem',
                            }}
                          >
                            {chat.title || 'Untitled Conversation'}
                          </Typography>
                          <Typography
                            variant="caption"
                            sx={{
                              ml: 2,
                              px: 1,
                              py: 0.375,
                              borderRadius: 1,
                              fontSize: '0.6875rem',
                              letterSpacing: '0.01em',
                              bgcolor:
                                theme.palette.mode === 'dark'
                                  ? alpha(theme.palette.primary.dark, 0.2)
                                  : alpha(theme.palette.primary.light, 0.15),
                              color:
                                theme.palette.mode === 'dark'
                                  ? theme.palette.primary.light
                                  : theme.palette.primary.dark,
                              border: `1px solid ${
                                theme.palette.mode === 'dark'
                                  ? alpha(theme.palette.primary.main, 0.2)
                                  : alpha(theme.palette.primary.main, 0.1)
                              }`,
                            }}
                          >
                            {formatDistanceToNow(chat.createdAt)}
                          </Typography>
                        </Box>
                        <Typography
                          variant="body2"
                          color="text.secondary"
                          sx={{
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            display: '-webkit-box',
                            WebkitLineClamp: 2,
                            WebkitBoxOrient: 'vertical',
                            lineHeight: 1.5,
                            fontSize: '0.8125rem',
                            opacity: 0.85,
                          }}
                        >
                          {lastMessage}
                        </Typography>
                      </Box>
                    </ListItemButton>
                  </ListItem>
                );
              })}
            </List>
          )}
        </DialogContent>
      </Dialog>

      <Snackbar
        open={snackbar.open}
        autoHideDuration={4000}
        onClose={() => setSnackbar({ ...snackbar, open: false })}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        TransitionComponent={Fade}
      >
        <Alert
          onClose={() => setSnackbar({ ...snackbar, open: false })}
          severity={snackbar.severity as AlertColor}
          variant="filled"
          elevation={6}
          sx={{
            width: '100%',
            borderRadius: 2,
            boxShadow:
              theme.palette.mode === 'dark'
                ? `0 4px 20px 0 ${alpha(theme.palette.common.black, 0.5)}`
                : `0 4px 20px 0 ${alpha(theme.palette.common.black, 0.2)}`,
          }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </>
  );
};

export default ArchivedChatsDialog;
