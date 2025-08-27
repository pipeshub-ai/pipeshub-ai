import React, { useState } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Box,
  Typography,
  IconButton,
  useTheme,
  alpha,
  TextField,
  Autocomplete,
  Chip,
  Stack,
  Avatar,
  MenuItem,
  CircularProgress,
  Fade,
} from '@mui/material';
import { Icon } from '@iconify/react';
import closeIcon from '@iconify-icons/eva/close-outline';
import shareIcon from '@iconify-icons/mdi/share-outline';
import accountGroupIcon from '@iconify-icons/mdi/account-group';
import personIcon from '@iconify-icons/eva/person-add-fill';
import { userChipStyle, groupChipStyle } from '../../utils/agent';

// Types
interface ShareAgentDialogProps {
  open: boolean;
  onClose: () => void;
  onShare: (shareData: ShareData) => Promise<void>;
  agentName: string;
  users: User[];
  groups: Group[];
  sharing?: boolean;
}

interface User {
  _id: string;
  fullName: string;
  email: string;
}

interface Group {
  _id: string;
  name: string;
}

interface ShareData {
  userIds: string[];
  groupIds: string[];
  permissions: 'view' | 'edit' | 'admin';
  message?: string;
}

// Helper function to get initials
const getInitials = (fullName: string) =>
  fullName
    .split(' ')
    .map((n) => n[0])
    .join('')
    .toUpperCase();

const ShareAgentDialog: React.FC<ShareAgentDialogProps> = ({
  open,
  onClose,
  onShare,
  agentName,
  users,
  groups,
  sharing = false,
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const [selectedUsers, setSelectedUsers] = useState<User[]>([]);
  const [selectedGroups, setSelectedGroups] = useState<Group[]>([]);
  const [permissions, setPermissions] = useState<'view' | 'edit' | 'admin'>('view');
  const [message, setMessage] = useState<string>('');
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);

  // Reset form when dialog closes
  const handleClose = () => {
    if (!isSubmitting) {
      setSelectedUsers([]);
      setSelectedGroups([]);
      setPermissions('view');
      setMessage('');
      onClose();
    }
  };

  const handleShare = async () => {
    if (selectedUsers.length === 0 && selectedGroups.length === 0) return;

    setIsSubmitting(true);
    try {
      await onShare({
        userIds: selectedUsers.map(user => user._id),
        groupIds: selectedGroups.map(group => group._id),
        permissions,
        message: message.trim() || undefined,
      });
      
      handleClose();
    } catch (error) {
      console.error('Error sharing agent:', error);
    } finally {
      setIsSubmitting(false);
    }
  };

  // Get avatar color based on name
  const getAvatarColor = (name: string) => {
    const colors = [
      theme.palette.primary.main,
      theme.palette.info.main,
      theme.palette.success.main,
      theme.palette.warning.main,
      theme.palette.error.main,
    ];

    const hash = name.split('').reduce((acc, char) => char.charCodeAt(0) + (acc * 32 - acc), 0);
    return colors[Math.abs(hash) % colors.length];
  };

  const permissionOptions = [
    { value: 'view', label: 'View Only', description: 'Can view and use the agent' },
    { value: 'edit', label: 'Edit Access', description: 'Can view, use, and modify the agent' },
    { value: 'admin', label: 'Admin Access', description: 'Full control including sharing permissions' },
  ];

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth="sm"
      fullWidth
      TransitionComponent={Fade}
      BackdropProps={{
        sx: {
          backdropFilter: 'blur(1px)',
          backgroundColor: alpha(theme.palette.common.black, 0.3),
        },
      }}
      PaperProps={{
        sx: {
          borderRadius: 1,
          boxShadow: '0 10px 35px rgba(0, 0, 0, 0.1)',
          overflow: 'hidden',
        },
      }}
    >
      <DialogTitle
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          p: 2.5,
          pl: 3,
          color: theme.palette.text.primary,
          borderBottom: '1px solid',
          borderColor: theme.palette.divider,
          fontWeight: 500,
          fontSize: '1rem',
          m: 0,
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: 32,
              height: 32,
              borderRadius: '6px',
              bgcolor: alpha(theme.palette.primary.main, 0.1),
              color: theme.palette.primary.main,
            }}
          >
            <Icon icon={shareIcon} width={18} height={18} />
          </Box>
          Share {agentName}
        </Box>

        <IconButton
          onClick={handleClose}
          size="small"
          sx={{ color: theme.palette.text.secondary }}
          aria-label="close"
          disabled={isSubmitting}
        >
          <Icon icon={closeIcon} width={20} height={20} />
        </IconButton>
      </DialogTitle>

      <DialogContent
        sx={{
          p: 0,
          '&.MuiDialogContent-root': {
            pt: 3,
            px: 3,
            pb: 0,
          },
        }}
      >
        <Box sx={{ mb: 3 }}>
          {/* Agent Info */}

          {/* Users Selection */}
          <Box sx={{ mb: 2 }}>
            <Autocomplete
              multiple
              limitTags={3}
              options={users}
              getOptionLabel={(option) => option.fullName || 'Unknown User'}
              renderInput={(params) => (
                <TextField
                  {...params}
                  label="Select Users"
                  placeholder="Choose users to share with..."
                  sx={{
                    '& .MuiOutlinedInput-root': {
                      borderRadius: 1,
                      backgroundColor: alpha(theme.palette.background.default, 0.3),
                      '& fieldset': {
                        borderColor: alpha(theme.palette.divider, 0.2),
                      },
                      '&:hover fieldset': {
                        borderColor: alpha(theme.palette.primary.main, 0.5),
                      },
                    },
                  }}
                  InputProps={{
                    ...params.InputProps,
                    startAdornment: (
                      <>
                        <Box mr={1} display="flex" alignItems="center">
                          <Icon
                            icon={personIcon}
                            width={18}
                            height={18}
                            style={{ color: theme.palette.text.secondary }}
                          />
                        </Box>
                        {params.InputProps.startAdornment}
                      </>
                    ),
                  }}
                />
              )}
              onChange={(_, newValue) => setSelectedUsers(newValue)}
              value={selectedUsers}
              renderTags={(value, getTagProps) =>
                value.map((option, index) => (
                  <Chip
                    label={option.fullName || 'Unknown User'}
                    {...getTagProps({ index })}
                    size="small"
                    sx={userChipStyle(isDark, theme)}
                  />
                ))
              }
              renderOption={(props, option) => (
                <MenuItem
                  {...props}
                  sx={{
                    py: 1,
                    px: 1.5,
                    borderRadius: 1,
                    my: 0.25,
                    '&:hover': {
                      bgcolor: alpha(theme.palette.action.hover, 0.1),
                    },
                    '&.Mui-selected': {
                      bgcolor: alpha(theme.palette.primary.main, 0.2),
                      '&:hover': {
                        bgcolor: alpha(theme.palette.primary.main, 0.25),
                      },
                    },
                  }}
                >
                  <Stack direction="row" alignItems="center" spacing={1.5}>
                    <Avatar
                      sx={{
                        width: 24,
                        height: 24,
                        fontSize: '0.75rem',
                        bgcolor: getAvatarColor(option.fullName || 'U'),
                      }}
                    >
                      {getInitials(option.fullName || 'U')}
                    </Avatar>
                    <Box>
                      <Typography
                        variant="body2"
                        sx={{
                          fontWeight: 500,
                          color: theme.palette.text.primary,
                        }}
                      >
                        {option.fullName || 'Unknown User'}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {option.email || 'No email'}
                      </Typography>
                    </Box>
                  </Stack>
                </MenuItem>
              )}
            />
          </Box>

          {/* Groups Selection */}
          <Box sx={{ mb: 2 }}>
            <Autocomplete
              multiple
              limitTags={3}
              options={groups}
              getOptionLabel={(option) => option.name}
              renderInput={(params) => (
                <TextField
                  {...params}
                  label="Select Groups"
                  placeholder="Choose groups to share with..."
                  sx={{
                    '& .MuiOutlinedInput-root': {
                      borderRadius: 1,
                      backgroundColor: alpha(theme.palette.background.default, 0.3),
                      '& fieldset': {
                        borderColor: alpha(theme.palette.divider, 0.2),
                      },
                      '&:hover fieldset': {
                        borderColor: alpha(theme.palette.info.main, 0.5),
                      },
                    },
                  }}
                  InputProps={{
                    ...params.InputProps,
                    startAdornment: (
                      <>
                        <Box mr={1} display="flex" alignItems="center">
                          <Icon
                            icon={accountGroupIcon}
                            width={18}
                            height={18}
                            style={{ color: theme.palette.text.secondary }}
                          />
                        </Box>
                        {params.InputProps.startAdornment}
                      </>
                    ),
                  }}
                />
              )}
              onChange={(_, newValue) => setSelectedGroups(newValue)}
              value={selectedGroups}
              renderTags={(value, getTagProps) =>
                value.map((option, index) => (
                  <Chip
                    label={option.name}
                    {...getTagProps({ index })}
                    size="small"
                    sx={groupChipStyle(isDark, theme)}
                  />
                ))
              }
              renderOption={(props, option) => (
                <MenuItem
                  {...props}
                  sx={{
                    py: 1,
                    px: 1.5,
                    borderRadius: 1,
                    my: 0.25,
                    '&:hover': {
                      bgcolor: alpha(theme.palette.action.hover, 0.1),
                    },
                    '&.Mui-selected': {
                      bgcolor: alpha(theme.palette.info.main, 0.2),
                      '&:hover': {
                        bgcolor: alpha(theme.palette.info.main, 0.25),
                      },
                    },
                  }}
                >
                  <Stack direction="row" alignItems="center" spacing={1.5}>
                    <Box
                      sx={{
                        width: 24,
                        height: 24,
                        borderRadius: '50%',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        bgcolor: alpha(theme.palette.info.main, 0.3),
                      }}
                    >
                      <Icon
                        icon={accountGroupIcon}
                        width={14}
                        height={14}
                        style={{ color: theme.palette.info.main }}
                      />
                    </Box>
                    <Typography
                      variant="body2"
                      sx={{
                        fontWeight: 500,
                        color: theme.palette.text.primary,
                      }}
                    >
                      {option.name}
                    </Typography>
                  </Stack>
                </MenuItem>
              )}
            />
          </Box>

          {/* Permission Level */}
          <Box sx={{ mb: 2 }}>
            <TextField
              select
              fullWidth
              label="Permission Level"
              value={permissions}
              onChange={(e) => setPermissions(e.target.value as 'view' | 'edit' | 'admin')}
              sx={{
                '& .MuiOutlinedInput-root': {
                  borderRadius: 1,
                  backgroundColor: alpha(theme.palette.background.default, 0.3),
                },
              }}
            >
              {permissionOptions.map((option) => (
                <MenuItem key={option.value} value={option.value}>
                  <Box>
                    <Typography variant="body2" sx={{ fontWeight: 500 }}>
                      {option.label}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {option.description}
                    </Typography>
                  </Box>
                </MenuItem>
              ))}
            </TextField>
          </Box>

          {/* Optional Message */}
          <Box sx={{ mb: 2 }}>
            <TextField
              fullWidth
              multiline
              rows={3}
              label="Message (Optional)"
              placeholder="Add a note about why you're sharing this agent..."
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              sx={{
                '& .MuiOutlinedInput-root': {
                  borderRadius: 1,
                  backgroundColor: alpha(theme.palette.background.default, 0.3),
                },
              }}
            />
          </Box>

          {/* Summary */}
          {(selectedUsers.length > 0 || selectedGroups.length > 0) && (
            <Box
              sx={{
                p: 2,
                borderRadius: 1,
                bgcolor: alpha(theme.palette.success.main, 0.08),
                border: `1px solid ${alpha(theme.palette.success.main, 0.2)}`,
              }}
            >
              <Typography variant="body2" color="success.main" sx={{ fontWeight: 500 }}>
                ✨ Ready to share with {selectedUsers.length + selectedGroups.length} recipient(s)
              </Typography>
              <Typography variant="caption" color="text.secondary">
                They will receive {permissions} access to this agent
              </Typography>
            </Box>
          )}
        </Box>
      </DialogContent>

      <DialogActions
        sx={{
          p: 2.5,
          borderTop: '1px solid',
          borderColor: theme.palette.divider,
          bgcolor: alpha(theme.palette.background.default, 0.5),
        }}
      >
        <Button
          variant="text"
          onClick={handleClose}
          disabled={isSubmitting}
          sx={{
            color: theme.palette.text.secondary,
            fontWeight: 500,
            '&:hover': {
              backgroundColor: alpha(theme.palette.divider, 0.8),
            },
          }}
        >
          Cancel
        </Button>

        <Button
          onClick={handleShare}
          variant="contained"
          color="primary"
          disabled={
            (selectedUsers.length === 0 && selectedGroups.length === 0) || 
            isSubmitting
          }
          startIcon={
            isSubmitting ? (
              <CircularProgress size={16} color="inherit" />
            ) : (
              <Icon icon={shareIcon} width={18} height={18} />
            )
          }
          sx={{
            bgcolor: theme.palette.primary.main,
            boxShadow: 'none',
            fontWeight: 500,
            '&:hover': {
              bgcolor: theme.palette.primary.dark,
              boxShadow: 'none',
            },
            '&.Mui-disabled': {
              bgcolor: alpha(theme.palette.primary.main, 0.3),
              color: alpha(theme.palette.primary.contrastText, 0.5),
            },
            px: 3,
          }}
        >
          {isSubmitting ? 'Sharing...' : 'Share Agent'}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default ShareAgentDialog;