import type { Conversation } from 'src/types/chat-bot';
import type {
  SnackbarState,
  ChatSidebarProps,
  ConversationGroup,
  DeleteDialogState,
  ConversationsResponse,
} from 'src/types/chat-sidebar';

import { Icon } from '@iconify/react';
import editIcon from '@iconify-icons/mdi/edit';
import menuIcon from '@iconify-icons/mdi/menu';
import checkIcon from '@iconify-icons/mdi/check';
import closeIcon from '@iconify-icons/mdi/close';
import shareIcon from '@iconify-icons/mdi/share';
import deleteIcon from '@iconify-icons/mdi/delete';
import chatIcon from '@iconify-icons/mdi/chat-outline';
import dotsIcon from '@iconify-icons/mdi/dots-vertical';
import archiveIcon from '@iconify-icons/mdi/archive-outline';
import messageIcon from '@iconify-icons/mdi/message-outline';
import emptyIcon from '@iconify-icons/mdi/comment-question-outline';
import React, { useRef, useMemo, useState, useEffect, useCallback } from 'react';

import {
  Box,
  List,
  Menu,
  Alert,
  alpha,
  Button,
  Tooltip,
  ListItem,
  MenuItem,
  Snackbar,
  useTheme,
  TextField,
  Typography,
  IconButton,
  ListItemButton,
  CircularProgress,
} from '@mui/material';

import axiosInstance from 'src/utils/axios';

import ArchivedChatsDialog from './dialogs/archieve-chat-dialog';
import ShareConversationDialog from './dialogs/share-conversation-dialog';
import { createScrollableContainerStyle } from '../utils/styles/scrollbar';
import DeleteConversationDialog from './dialogs/delete-conversation-dialog';

const ChatSidebar = ({
  onClose,
  onChatSelect,
  onNewChat,
  selectedId,
  shouldRefresh,
  onRefreshComplete,
}: ChatSidebarProps) => {
  // Use useRef for values that shouldn't trigger re-renders when changed
  const isMounted = useRef(true);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [page, setPage] = useState<number>(1);
  const [hasMore, setHasMore] = useState<boolean>(true);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [initialLoading, setInitialLoading] = useState<boolean>(true);
  const [menuAnchor, setMenuAnchor] = useState<HTMLElement | null>(null);
  const [selectedChat, setSelectedChat] = useState<Conversation | null>(null);
  const [editingChat, setEditingChat] = useState<Conversation | null>(null);
  const [editTitle, setEditTitle] = useState<string>('');
  const [deleteDialog, setDeleteDialog] = useState<DeleteDialogState>({ open: false, chat: null });
  const [isDeleting, setIsDeleting] = useState<boolean>(false);
  const [isShareDialogOpen, setIsShareDialogOpen] = useState<boolean>(false);
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<'my' | 'shared'>('my');
  const [snackbar, setSnackbar] = useState<SnackbarState>({
    open: false,
    message: '',
    severity: 'success',
  });

  const [archiveDialogOpen, setArchiveDialogOpen] = useState<boolean>(false);
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const scrollableStyles = createScrollableContainerStyle(theme);
  // Memoize fetch function to prevent recreation on each render
  const fetchConversations = useCallback(
    async (pageNum: number): Promise<void> => {
      if (!isMounted.current) return;

      setIsLoading(true);
      try {
        const response = await axiosInstance.get<ConversationsResponse>('/api/v1/conversations/', {
          params: {
            page: pageNum,
            limit: 20,
            shared: activeTab === 'shared',
          },
        });

        const {
          conversations: newConversations = [],
          pagination = {
            page: 1,
            limit: 20,
            totalCount: 0,
            totalPages: 1,
            hasNextPage: false,
            hasPrevPage: false,
          },
        } = response.data;

        if (!isMounted.current) return;

        if (pageNum === 1) {
          setConversations(newConversations);
        } else {
          setConversations((prev) => [...prev, ...newConversations]);
        }

        setHasMore(pagination.hasNextPage || false);
        setPage(pageNum);
      } catch (error) {
        if (!isMounted.current) return;

        setHasMore(false);
        // setSnackbar({
        //   open: true,
        //   message: 'Failed to fetch conversations. Please try again later.',
        //   severity: 'error',
        // });
      } finally {
        if (isMounted.current) {
          setIsLoading(false);
          setInitialLoading(false);
        }
      }
    },
    [activeTab]
  ); // Only depend on activeTab

  // Clean up effect to prevent state updates after unmount
  useEffect(() => {
    isMounted.current = true;
    return () => {
      isMounted.current = false;
    };
  }, []);

  // Handle refresh logic
  useEffect(() => {
    if (shouldRefresh && isMounted.current) {
      fetchConversations(1).then(() => {
        if (isMounted.current && onRefreshComplete) {
          onRefreshComplete();
        }
      });
    }
  }, [shouldRefresh, fetchConversations, onRefreshComplete]);

  // Initial fetch and tab change effect
  useEffect(() => {
    if (isMounted.current) {
      setPage(1);
      setInitialLoading(true);
      fetchConversations(1);
    }
  }, [activeTab, fetchConversations]);

  // Menu handling
  const handleMenuOpen = useCallback(
    (event: React.MouseEvent<HTMLButtonElement>, chat: Conversation): void => {
      event.stopPropagation();
      setMenuAnchor(event.currentTarget);
      setSelectedChat(chat);
    },
    []
  );

  const handleMenuClose = useCallback(() => {
    setMenuAnchor(null);
    setSelectedChat(null);
  }, []);

  // Memoize grouped conversations to prevent unnecessary recalculations
  const groupedConversations = useMemo(() => {
    const groups: ConversationGroup = {
      Today: [],
      Yesterday: [],
      'Previous 7 days': [],
      'Previous 30 days': [],
      Older: [],
    };

    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const yesterday = new Date(today);
    yesterday.setDate(yesterday.getDate() - 1);
    const weekAgo = new Date(today);
    weekAgo.setDate(weekAgo.getDate() - 7);
    const monthAgo = new Date(today);
    monthAgo.setDate(monthAgo.getDate() - 30);

    conversations.forEach((chat) => {
      const chatDate = new Date(chat.lastActivityAt || chat.createdAt);

      if (chatDate >= today) {
        groups.Today.push(chat);
      } else if (chatDate >= yesterday) {
        groups.Yesterday.push(chat);
      } else if (chatDate >= weekAgo) {
        groups['Previous 7 days'].push(chat);
      } else if (chatDate >= monthAgo) {
        groups['Previous 30 days'].push(chat);
      } else {
        groups.Older.push(chat);
      }
    });

    // Return only groups that have conversations
    return Object.fromEntries(Object.entries(groups).filter(([_, chats]) => chats.length > 0));
  }, [conversations]);

  const handleListItemClick = useCallback(
    (chat: Conversation): void => {
      if (onChatSelect) onChatSelect(chat);
    },
    [onChatSelect]
  );

  const handleNewChat = useCallback((): void => {
    handleMenuClose();
    if (onNewChat) onNewChat();
  }, [handleMenuClose, onNewChat]);

  const handleScroll = useCallback(
    (e: React.UIEvent<HTMLDivElement>): void => {
      if (!hasMore || isLoading) return;

      const target = e.target as HTMLDivElement;
      const bottom = Math.abs(target.scrollHeight - target.scrollTop - target.clientHeight) < 1;

      if (bottom) {
        const nextPage = page + 1;
        setPage(nextPage);
        fetchConversations(nextPage);
      }
    },
    [hasMore, isLoading, page, fetchConversations]
  );

  const handleEditStart = useCallback(
    (chat: Conversation): void => {
      setEditingChat(chat);
      setEditTitle(chat.title || '');
      handleMenuClose();
    },
    [handleMenuClose]
  );

  const handleEditCancel = useCallback((): void => {
    setEditingChat(null);
    setEditTitle('');
  }, []);

  const handleEditSubmit = useCallback(
    async (e: React.FormEvent): Promise<void> => {
      e.preventDefault();
      if (!editingChat || !editTitle.trim()) return;

      try {
        await axiosInstance.patch(`/api/v1/conversations/${editingChat._id}/title`, {
          title: editTitle.trim(),
        });

        // Optimistically update the UI immediately
        setConversations((prev) =>
          prev.map((chat) =>
            chat._id === editingChat._id ? { ...chat, title: editTitle.trim() } : chat
          )
        );

        setSnackbar({
          open: true,
          message: 'Conversation renamed successfully',
          severity: 'success',
        });
        handleEditCancel();
      } catch (error) {
        // In case of failure, show an error but don't revert the UI
        // to avoid flickering - when the list refreshes it will get the correct state
        // setSnackbar({
        //   open: true,
        //   message: 'Failed to rename conversation. Please try again.',
        //   severity: 'error',
        // });
      }
    },
    [editingChat, editTitle, handleEditCancel]
  );

  const handleDeleteClick = useCallback(
    (chat: Conversation): void => {
      setDeleteDialog({ open: true, chat });
      handleMenuClose();
    },
    [handleMenuClose]
  );

  const handleDeleteConfirm = useCallback(async (): Promise<void> => {
    if (!deleteDialog.chat) return;
    setIsDeleting(true);

    try {
      // Immediately update the UI by removing the conversation
      setConversations((prev) => prev.filter((chat) => chat._id !== deleteDialog.chat?._id));

      // Check if the deleted chat is the currently selected one
      const isCurrentChatDeleted = selectedId === deleteDialog.chat._id;

      // Send API request in the background
      await axiosInstance.delete(`/api/v1/conversations/${deleteDialog.chat._id}`);

      // If we deleted the current chat, navigate to a new conversation
      if (isCurrentChatDeleted && onNewChat) {
        onNewChat();
      }

      setSnackbar({
        open: true,
        message: 'Conversation deleted successfully',
        severity: 'success',
      });
    } catch (error) {
      // If the API request fails, fetch conversations again to restore the correct state
      fetchConversations(1);

      // setSnackbar({
      //   open: true,
      //   message: 'Failed to delete conversation. Please try again.',
      //   severity: 'error',
      // });
    } finally {
      setIsDeleting(false);
      setDeleteDialog({ open: false, chat: null });
    }
  }, [deleteDialog.chat, selectedId, onNewChat, fetchConversations]);

  const handleArchive = useCallback(
    async (chat: Conversation): Promise<void> => {
      try {
        // First update UI optimistically
        setConversations((prev) => prev.filter((c) => c._id !== chat._id));

        // Check if the archived chat is the currently selected one
        const isCurrentChatArchived = selectedId === chat._id;

        // Make API call in background
        await axiosInstance.patch(`/api/v1/conversations/${chat._id}/archive`);

        // If we archived the current chat, navigate to a new conversation
        if (isCurrentChatArchived && onNewChat) {
          onNewChat();
        }

        handleMenuClose();

        setSnackbar({
          open: true,
          message: 'Conversation archived successfully',
          severity: 'success',
        });
      } catch (error) {
        // If API request fails, restore the list
        fetchConversations(1);

        // setSnackbar({
        //   open: true,
        //   message: 'Failed to archive conversation. Please try again.',
        //   severity: 'error',
        // });
      }
    },
    [fetchConversations, handleMenuClose, onNewChat, selectedId]
  );

  const handleUnarchive = useCallback(async (): Promise<void> => {
    // Just refresh the list after unarchiving
    await fetchConversations(1);
  }, [fetchConversations]);

  const handleShareConversation = useCallback(
    (conversationId: string): void => {
      setSelectedConversationId(conversationId);
      setIsShareDialogOpen(true);
      handleMenuClose();
    },
    [handleMenuClose]
  );

  const handleShareDialogClose = useCallback((): void => {
    setIsShareDialogOpen(false);
    setSelectedConversationId(null);
  }, []);

  // Memoize the EmptyState component to prevent unnecessary re-renders
  const EmptyState = useMemo(
    () => () => (
      <Box
        sx={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          textAlign: 'center',
          height: '100%',
          p: 3,
        }}
      >
        <Icon
          icon={emptyIcon}
          style={{
            fontSize: '68px',
            color: isDark
              ? alpha(theme.palette.common.white, 0.15)
              : alpha(theme.palette.common.black, 0.15),
            marginBottom: '16px',
          }}
        />
        <Typography
          variant="h6"
          sx={{
            mb: 1,
            fontWeight: 500,
            color: theme.palette.text.primary,
            fontSize: '1.125rem',
          }}
        >
          {activeTab === 'my' ? 'No conversations yet' : 'No shared conversations'}
        </Typography>
        <Typography
          variant="body2"
          color="text.secondary"
          sx={{
            mb: 3,
            maxWidth: '240px',
            opacity: isDark ? 0.7 : 0.8,
            lineHeight: 1.5,
          }}
        >
          {activeTab === 'my'
            ? 'Start a new conversation to begin chatting with PipesHub Agent'
            : 'When someone shares a conversation with you, it will appear here'}
        </Typography>
        {activeTab === 'my' && (
          <Button
            variant="contained"
            disableElevation
            startIcon={<Icon icon={chatIcon} style={{ fontSize: '18px' }} />}
            onClick={handleNewChat}
            sx={{
              textTransform: 'none',
              borderRadius: 1,
              px: 2.5,
              py: 0.75,
              fontWeight: 500,
              fontSize: '0.875rem',
              boxShadow: 'none',
              bgcolor: isDark ? theme.palette.primary.dark : theme.palette.primary.main,
              '&:hover': {
                boxShadow: 'none',
                bgcolor: isDark
                  ? alpha(theme.palette.primary.main, 0.8)
                  : alpha(theme.palette.primary.main, 0.9),
              },
            }}
          >
            Start a conversation
          </Button>
        )}
      </Box>
    ),
    // eslint-disable-next-line
    [activeTab, handleNewChat]
  );
  // Memoize the ChatItem component to prevent unnecessary re-renders
  const renderChatItem = useCallback(
    (chat: Conversation) => (
      <ListItem
        key={chat._id}
        disablePadding
        secondaryAction={
          editingChat?._id !== chat._id && (
            <IconButton edge="end" size="small" onClick={(e) => handleMenuOpen(e, chat)}>
              <Icon icon={dotsIcon} />
            </IconButton>
          )
        }
      >
        {editingChat?._id === chat._id ? (
          <Box
            component="form"
            onSubmit={handleEditSubmit}
            sx={{
              display: 'flex',
              alignItems: 'center',
              gap: 1,
              width: '100%',
              px: 1,
            }}
          >
            <TextField
              size="small"
              value={editTitle}
              onChange={(e) => setEditTitle(e.target.value)}
              autoFocus
              fullWidth
              variant="outlined"
              placeholder="Enter conversation title"
              sx={{
                '& .MuiOutlinedInput-root': {
                  borderRadius: 1,
                },
              }}
            />
            <IconButton size="small" color="primary" type="submit" disabled={!editTitle.trim()}>
              <Icon icon={checkIcon} />
            </IconButton>
            <IconButton size="small" onClick={handleEditCancel}>
              <Icon icon={closeIcon} />
            </IconButton>
          </Box>
        ) : (
          <ListItemButton
            selected={selectedId === chat._id}
            onClick={() => handleListItemClick(chat)}
            sx={{
              borderRadius: 1,
              py: 1,
              '&.Mui-selected': {
                bgcolor: 'action.selected',
              },
              '&:hover': {
                bgcolor: 'action.hover',
              },
            }}
          >
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, minWidth: 0 }}>
              <Icon icon={messageIcon} />
              <Typography
                variant="body2"
                noWrap
                sx={{
                  flex: 1,
                  color: 'text.primary',
                }}
              >
                {chat.title || 'New conversation'}
              </Typography>
            </Box>
          </ListItemButton>
        )}
      </ListItem>
    ),
    [
      editingChat,
      editTitle,
      handleEditCancel,
      handleEditSubmit,
      handleMenuOpen,
      handleListItemClick,
      selectedId,
    ]
  );

  // Memoize menu items to prevent recreating this array on every render
  const menuItems = useMemo(
    () =>
      [
        {
          icon: editIcon,
          label: 'Rename',
          onClick: () => selectedChat && handleEditStart(selectedChat),
          show: activeTab === 'my',
        },
        {
          icon: archiveIcon,
          label: 'Archive',
          onClick: () => selectedChat && handleArchive(selectedChat),
          show: activeTab === 'my',
        },
        {
          icon: deleteIcon,
          label: 'Delete',
          onClick: () => selectedChat && handleDeleteClick(selectedChat),
          color: 'error',
          show: activeTab === 'my',
        },
        {
          icon: shareIcon,
          label: 'Share',
          onClick: () => selectedChat && handleShareConversation(selectedChat._id),
          color: 'primary',
          show: activeTab === 'my',
        },
      ].filter((item) => item.show),
    [
      activeTab,
      selectedChat,
      handleEditStart,
      handleArchive,
      handleDeleteClick,
      handleShareConversation,
    ]
  );

  return (
    <Box
      sx={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        bgcolor: 'background.default',
        ...scrollableStyles,
      }}
    >
      {/* Header */}
      <Box sx={{ p: 1.68, display: 'flex', alignItems: 'center', gap: 1 }}>
        <IconButton size="small" onClick={onClose}>
          <Icon icon={menuIcon} />
        </IconButton>
        <Typography variant="h6" sx={{ flex: 1 }}>
          PipesHub Agent
        </Typography>
        <>
          <Tooltip title="Archived Chats">
            <IconButton size="small" onClick={() => setArchiveDialogOpen(true)}>
              <Icon icon={archiveIcon} />
            </IconButton>
          </Tooltip>
          <Tooltip title="New Chat">
            <IconButton size="small" onClick={handleNewChat}>
              <Icon icon={chatIcon} />
            </IconButton>
          </Tooltip>
        </>
      </Box>
      <Box>
        <Box
          sx={{
            display: 'flex',
            gap: 1,
            borderBottom: 1,
            borderColor: 'divider',
          }}
        >
          <Box
            onClick={() => setActiveTab('my')}
            sx={{
              py: 1,
              px: 2,
              cursor: 'pointer',
              borderBottom: 2,
              borderColor: activeTab === 'my' ? 'primary.main' : 'transparent',
              color: activeTab === 'my' ? 'primary.main' : 'text.secondary',
              '&:hover': {
                color: 'primary.main',
              },
              transition: 'all 0.2s',
            }}
          >
            <Typography variant="body2" sx={{ fontWeight: activeTab === 'my' ? 600 : 400 }}>
              My Conversations
            </Typography>
          </Box>
          <Box
            onClick={() => setActiveTab('shared')}
            sx={{
              py: 1,
              px: 2,
              cursor: 'pointer',
              borderBottom: 2,
              borderColor: activeTab === 'shared' ? 'primary.main' : 'transparent',
              color: activeTab === 'shared' ? 'primary.main' : 'text.secondary',
              '&:hover': {
                color: 'primary.main',
              },
              transition: 'all 0.2s',
            }}
          >
            <Typography variant="body2" sx={{ fontWeight: activeTab === 'shared' ? 600 : 400 }}>
              Shared with Me
            </Typography>
          </Box>
        </Box>
      </Box>

      {/* Conversations List */}
      <Box
        sx={{
          flexGrow: 1,
          px: 1,
          display: 'flex',
          flexDirection: 'column',
          ...scrollableStyles,
        }}
        onScroll={handleScroll}
      >
        {initialLoading ? (
          <Box
            sx={{
              display: 'flex',
              justifyContent: 'center',
              alignItems: 'center',
              minHeight: 200,
              width: '100%',
              py: 4,
            }}
          >
            <CircularProgress size={36} />
          </Box>
        ) : Object.keys(groupedConversations).length === 0 ? (
          <EmptyState />
        ) : (
          Object.entries(groupedConversations).map(([group, chats]) => (
            <React.Fragment key={group}>
              <Typography
                variant="caption"
                sx={{
                  px: 2,
                  py: 1,
                  display: 'block',
                  color: 'text.secondary',
                  fontWeight: 500,
                }}
              >
                {group}
              </Typography>
              <List dense disablePadding>
                {chats.map(renderChatItem)}
              </List>
            </React.Fragment>
          ))
        )}

        {isLoading && !initialLoading && (
          <Box display="flex" justifyContent="center" p={2}>
            <CircularProgress size={24} />
          </Box>
        )}
      </Box>

      {/* Context Menu */}
      <Menu
        anchorEl={menuAnchor}
        open={Boolean(menuAnchor)}
        onClose={handleMenuClose}
        anchorOrigin={{ vertical: 'center', horizontal: 'right' }}
        transformOrigin={{ vertical: 'top', horizontal: 'left' }}
      >
        {menuItems.map((item) => (
          <MenuItem key={item.label} onClick={item.onClick} sx={{ gap: 1 }}>
            <Icon icon={item.icon} />
            {item.label}
          </MenuItem>
        ))}
      </Menu>

      {/* Delete Dialog */}
      <DeleteConversationDialog
        open={deleteDialog.open}
        onClose={() => setDeleteDialog({ open: false, chat: null })}
        onConfirm={handleDeleteConfirm}
        title={deleteDialog.chat?.title}
        isDeleting={isDeleting}
      />
      <ArchivedChatsDialog
        open={archiveDialogOpen}
        onClose={() => setArchiveDialogOpen(false)}
        onUnarchive={handleUnarchive}
        onSelectChat={onChatSelect}
      />

      <ShareConversationDialog
        open={isShareDialogOpen}
        onClose={handleShareDialogClose}
        conversationId={selectedConversationId}
      />

      {/* Snackbar */}
      <Snackbar
        open={snackbar.open}
        autoHideDuration={4000}
        onClose={() => setSnackbar({ ...snackbar, open: false })}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
      >
        <Alert
          onClose={() => setSnackbar({ ...snackbar, open: false })}
          severity={snackbar.severity}
          variant="filled"
          elevation={6}
          sx={{ width: '100%' }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Box>
  );
};

export default ChatSidebar;
