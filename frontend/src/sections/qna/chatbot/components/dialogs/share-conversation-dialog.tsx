import type { User } from 'src/context/UserContext';
import type { SnackbarState } from 'src/types/chat-sidebar';

import { Icon } from '@iconify/react';
import React, { useState } from 'react';
import closeIcon from '@iconify-icons/mdi/close';
import magnifyIcon from '@iconify-icons/mdi/magnify';
import shareIcon from '@iconify-icons/mdi/share-variant-outline';

import { 
  Box,
  Fade,
  Paper,
  Alert,
  alpha,
  Dialog,
  Button,
  Checkbox,
  Snackbar,
  MenuItem,
  useTheme,
  TextField,
  Typography,
  IconButton,
  DialogTitle,
  Autocomplete,
  DialogContent,
  DialogActions,
  CircularProgress,
} from '@mui/material';

import axiosInstance from 'src/utils/axios';

import { useUsers } from 'src/context/UserContext';

interface ShareConversationDialogProps {
  open: boolean;
  onClose: () => void;
  conversationId: string | null;
}

const ShareConversationDialog = ({
  open,
  onClose,
  conversationId,
}: ShareConversationDialogProps) => {
  const users = useUsers();
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  
  const [selectedUsers, setSelectedUsers] = useState<User[]>([]);
  const [shareLink, setShareLink] = useState<string>('');
  const [isShared, setIsShared] = useState<boolean>(false);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [copySuccess, setCopySuccess] = useState<boolean>(false);
  const [snackbarState, setSnackbarState] = useState<SnackbarState>({
    open: false,
    message: '',
    severity: 'success',
  });
  
  const handleShareConversation = async () => {
    if (selectedUsers.length === 0) return;
    
    setIsLoading(true);
    try {
      const response = await axiosInstance.post(`/api/v1/conversations/${conversationId}/share`, {
        isPublic: true,
        userIds: selectedUsers.map((user) => user._id),
      });

      setShareLink(response.data.shareLink);
      setIsShared(true);
      setSnackbarState({
        open: true,
        message: 'Conversation shared successfully',
        severity: 'success',
      });
    } catch (error) {
      setSnackbarState({
        open: true,
        message: 'Failed to share conversation',
        severity: 'error',
      });
    } finally {
      setIsLoading(false);
    }
  };

  const handleCopyLink = async () => {
    try {
      await navigator.clipboard.writeText(shareLink);
      setCopySuccess(true);
      setTimeout(() => setCopySuccess(false), 2000);
      setSnackbarState({
        open: true,
        message: 'Link copied to clipboard',
        severity: 'success',
      });
    } catch (error) {
      setSnackbarState({
        open: true,
        message: 'Failed to copy link',
        severity: 'error',
      });
    }
  };

  const handleDialogClose = () => {
    setSelectedUsers([]);
    setShareLink('');
    setIsShared(false);
    onClose();
  };

  return (
    <>
      <Dialog
        open={open}
        onClose={handleDialogClose}
        maxWidth="sm"
        fullWidth
        TransitionComponent={Fade}
        BackdropProps={{
          sx: {
            backdropFilter: 'blur(1px)',
            backgroundColor: alpha(theme.palette.common.black, isDark ? 0.6 : 0.4),
          },
        }}
        PaperProps={{
          elevation: isDark ? 6 : 2,
          sx: { 
            borderRadius: 1.5,
            overflow: 'hidden',
          },
        }}
      >
        <DialogTitle
          sx={{ 
            px: 3, 
            py: 2, 
            borderBottom: '1px solid', 
            borderColor: alpha(theme.palette.divider, 0.08),
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between'
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
            <Icon 
              icon={shareIcon} 
              style={{
                fontSize: "18px",
                color: theme.palette.primary.main
              }}
            />
            <Typography 
              variant="h6" 
              sx={{ 
                fontWeight: 500, 
                fontSize: '1rem',
              }}
            >
              Share Conversation
            </Typography>
          </Box>
          <IconButton 
            onClick={handleDialogClose} 
            size="small"
            sx={{
              color: theme.palette.text.secondary,
              '&:hover': {
                color: theme.palette.text.primary,
                bgcolor: alpha(theme.palette.action.hover, isDark ? 0.2 : 0.1),
              }
            }}
          >
            <Icon icon={closeIcon} style={{ fontSize: "18px" }} />
          </IconButton>
        </DialogTitle>
        
        <DialogContent sx={{ px: 3, py: 2.5 }}>
          {/* Share Link Section */}
          {/* {(isShared || shareLink) && (
            <Box 
              sx={{ 
                bgcolor: isDark 
                  ? alpha(theme.palette.primary.dark, 0.05)
                  : alpha(theme.palette.primary.light, 0.05),
                p: 2, 
                borderRadius: 1.5,
                border: `1px solid ${alpha(theme.palette.primary.main, isDark ? 0.1 : 0.1)}`,
                mb: 3,
              }}
            >
              <Typography 
                variant="subtitle2" 
                sx={{ 
                  mb: 1.5,
                  fontWeight: 500,
                }}
              >
                Share Link
              </Typography>
              <Box
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 1,
                  backgroundColor: isDark 
                    ? alpha(theme.palette.background.paper, 0.7)
                    : theme.palette.background.paper,
                  borderRadius: 1,
                  border: '1px solid',
                  borderColor: alpha(theme.palette.divider, 0.1),
                  px: 1.5,
                  py: 1,
                }}
              >
                <Typography
                  variant="body2"
                  sx={{
                    flex: 1,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {shareLink}
                </Typography>
                <Tooltip title={copySuccess ? "Copied!" : "Copy link"}>
                  <IconButton
                    size="small"
                    onClick={handleCopyLink}
                    sx={{
                      color: copySuccess ? theme.palette.success.main : theme.palette.primary.main,
                      '&:hover': {
                        bgcolor: copySuccess
                          ? alpha(theme.palette.success.main, isDark ? 0.15 : 0.1)
                          : alpha(theme.palette.primary.main, isDark ? 0.15 : 0.1),
                      },
                    }}
                  >
                    <Icon 
                      icon={copySuccess ? checkCircleIcon : contentCopyIcon} 
                      style={{ fontSize: "18px" }} 
                    />
                  </IconButton>
                </Tooltip>
              </Box>
            </Box>
          )} */}

          {/* User Selection Section */}
          <Box>
            <Typography 
              variant="subtitle2" 
              sx={{ 
                my: 1.5,
                fontWeight: 500,
              }}
            >
              Share with Team Members
            </Typography>
            <Autocomplete
              multiple
              limitTags={3}
              options={users}
              getOptionLabel={(user) => user.fullName}
              value={selectedUsers}
              onChange={(event, newValue) => {
                setSelectedUsers(newValue);
              }}
              renderOption={(props, user, { selected }) => (
                <MenuItem 
                  {...props}
                  sx={{
                    py: 1,
                    px: 1.5,
                    borderRadius: 1,
                    my: 0.25,
                    '&:hover': {
                      bgcolor: isDark
                        ? alpha(theme.palette.action.hover, 0.1)
                        : theme.palette.action.hover,
                    }
                  }}
                >
                  <Checkbox 
                    checked={selected} 
                    color="primary" 
                    size="small"
                    sx={{ mr: 1 }}
                  />
                  <Box>
                    <Typography variant="body2" sx={{ fontWeight: 500 }}>
                      {user.fullName}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {user.email}
                    </Typography>
                  </Box>
                </MenuItem>
              )}
              renderInput={(params) => (
                <TextField
                  {...params}
                  variant="outlined"
                  placeholder="Search users by name or email"
                  fullWidth
                  sx={{
                    '& .MuiOutlinedInput-root': {
                      borderRadius: 1,
                      backgroundColor: isDark 
                        ? alpha(theme.palette.background.paper, 0.6)
                        : theme.palette.background.paper,
                      '& fieldset': {
                        borderColor: alpha(theme.palette.divider, 0.1),
                      },
                      '&:hover fieldset': {
                        borderColor: alpha(theme.palette.primary.main, 0.5),
                      },
                      '& .MuiOutlinedInput-input': {
                        padding: '10px 12px',
                      },
                      '& .MuiChip-root': {
                        borderRadius: 0.75,
                        height: 24,
                        fontSize: '0.75rem',
                        bgcolor: isDark 
                          ? alpha(theme.palette.primary.dark, 0.15)
                          : alpha(theme.palette.primary.light, 0.1),
                        color: theme.palette.primary.main,
                      }
                    },
                  }}
                  InputProps={{
                    ...params.InputProps,
                    startAdornment: (
                      <>
                        <Box mr={1} display="flex" alignItems="center">
                          <Icon 
                            icon={magnifyIcon} 
                            style={{
                              fontSize: "18px",
                              color: theme.palette.text.secondary
                            }}
                          />
                        </Box>
                        {params.InputProps.startAdornment}
                      </>
                    ),
                  }}
                />
              )}
              PaperComponent={({ children }) => (
                <Paper
                  sx={{
                    maxHeight: 220,
                    overflow: 'auto',
                    mt: 0.5,
                    borderRadius: 1,
                    boxShadow: isDark
                      ? `0 4px 20px ${alpha(theme.palette.common.black, 0.3)}`
                      : `0 4px 20px ${alpha(theme.palette.common.black, 0.1)}`,
                    border: `1px solid ${alpha(theme.palette.divider, 0.05)}`,
                  }}
                >
                  {children}
                </Paper>
              )}
            />
          </Box>

          <Box mt={3} pt={1.5} sx={{ borderTop: `1px solid ${alpha(theme.palette.divider, 0.08)}` }}>
            <Typography 
              variant="caption" 
              color="text.secondary"
              sx={{
                display: 'flex',
                alignItems: 'center',
                gap: 0.5,
              }}
            >
              <Box
                component="span"
                sx={{
                  display: 'inline-block',
                  width: 4,
                  height: 4,
                  borderRadius: '50%',
                  bgcolor: theme.palette.warning.main,
                }}
              />
              Sharing conversations with uploaded images is not supported
            </Typography>
          </Box>
        </DialogContent>
        
        <DialogActions
          sx={{ 
            px: 3, 
            py: 2, 
            borderTop: '1px solid', 
            borderColor: alpha(theme.palette.divider, 0.08),
            bgcolor: isDark 
              ? alpha(theme.palette.background.default, 0.3)
              : alpha(theme.palette.background.default, 0.2),
            gap: 1.5,
          }}
        >
          <Button
            onClick={handleDialogClose}
            variant="text"
            color="inherit"
            sx={{ 
              borderRadius: 1,
              textTransform: 'none',
              fontWeight: 500,
              fontSize: '0.875rem',
              color: theme.palette.text.secondary,
              '&:hover': {
                backgroundColor: alpha(theme.palette.action.hover, isDark ? 0.1 : 0.05),
                color: theme.palette.text.primary,
              }
            }}
          >
            Cancel
          </Button>
          
          {(!isShared || !shareLink) && (
            <Button
              onClick={handleShareConversation}
              variant="contained"
              disableElevation
              disabled={selectedUsers.length === 0 || isLoading}
              startIcon={
                isLoading ? (
                  <CircularProgress size={16} color="inherit" />
                ) : (
                  <Icon 
                    icon={shareIcon} 
                    style={{ fontSize: "18px" }} 
                  />
                )
              }
              sx={{ 
                borderRadius: 1,
                textTransform: 'none',
                fontWeight: 500,
                fontSize: '0.875rem',
                boxShadow: 'none',
                px: 2,
                py: 0.75,
                '&:hover': {
                  boxShadow: 'none',
                  bgcolor: isDark 
                    ? alpha(theme.palette.primary.main, 0.8)
                    : alpha(theme.palette.primary.main, 0.9),
                },
              }}
            >
              {isLoading ? 'Sharing...' : 'Share'}
            </Button>
          )}
        </DialogActions>
      </Dialog>

      <Snackbar
        open={snackbarState.open}
        autoHideDuration={3000}
        onClose={() => setSnackbarState((prev) => ({ ...prev, open: false }))}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        TransitionComponent={Fade}
      >
        <Alert
          onClose={() => setSnackbarState((prev) => ({ ...prev, open: false }))}
          severity={snackbarState.severity}
          variant="filled"
          elevation={2}
          sx={{ 
            borderRadius: 1,
            boxShadow: isDark
              ? `0 4px 16px ${alpha(theme.palette.common.black, 0.4)}`
              : `0 4px 16px ${alpha(theme.palette.common.black, 0.15)}`,
          }}
        >
          {snackbarState.message}
        </Alert>
      </Snackbar>
    </>
  );
}

export default ShareConversationDialog;