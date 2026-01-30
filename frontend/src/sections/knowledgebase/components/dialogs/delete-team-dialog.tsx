import React, { useState } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Box,
  Stack,
  Typography,
  Button,
  CircularProgress,
  alpha,
  useTheme,
  Alert,
  IconButton,
  Paper,
  Chip,
} from '@mui/material';
import { Icon } from '@iconify/react';
import warningIcon from '@iconify-icons/eva/alert-triangle-fill';
import deleteIcon from '@iconify-icons/mdi/delete-outline';
import closeIcon from '@iconify-icons/mdi/close';
import { Team } from '../../types/teams';

interface DeleteTeamDialogProps {
  open: boolean;
  team: Team | null;
  onClose: () => void;
  onSubmit: () => Promise<void>;
  onSuccess: (message: string) => void;
  onError: (message: string) => void;
}

const DeleteTeamDialog: React.FC<DeleteTeamDialogProps> = ({
  open,
  team,
  onClose,
  onSubmit,
  onSuccess,
  onError,
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async () => {
    setSubmitting(true);
    try {
      await onSubmit();
      onClose();
    } catch (err: any) {
      onError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={() => !submitting && onClose()}
      maxWidth="xs"
      fullWidth
      PaperProps={{
        sx: {
          borderRadius: 2.5,
          boxShadow: isDark 
            ? '0 24px 48px rgba(0, 0, 0, 0.4), 0 0 0 1px rgba(255, 255, 255, 0.05)'
            : '0 20px 60px rgba(0, 0, 0, 0.12)',
          overflow: 'hidden',
          border: isDark ? '1px solid rgba(255, 255, 255, 0.08)' : 'none',
        },
      }}
      slotProps={{
        backdrop: {
          sx: {
            backgroundColor: isDark ? 'rgba(0, 0, 0, 0.35)' : 'rgba(0, 0, 0, 0.5)',
          },
        },
      }}
    >
      <DialogTitle
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          px: 3,
          py: 2.5,
          backgroundColor: 'transparent',
          flexShrink: 0,
          borderBottom: isDark 
            ? `1px solid ${alpha(theme.palette.divider, 0.12)}`
            : `1px solid ${alpha(theme.palette.divider, 0.08)}`,
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
          <Box
            sx={{
              p: 1.25,
              borderRadius: 1.5,
              bgcolor: alpha(theme.palette.error.main, 0.12),
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              border: `1px solid ${alpha(theme.palette.error.main, 0.2)}`,
            }}
          >
            <Icon icon={warningIcon} width={24} height={24} style={{ color: theme.palette.error.main }} />
          </Box>
          <Box>
            <Typography
              variant="h6"
              sx={{ 
                fontWeight: 600, 
                mb: 0.5, 
                color: theme.palette.text.primary,
                fontSize: '1.125rem',
                letterSpacing: '-0.01em',
              }}
            >
              Delete Team
            </Typography>
            <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.75rem' }}>
              This action cannot be undone
            </Typography>
          </Box>
        </Box>

        <IconButton
          onClick={onClose}
          size="small"
          disabled={submitting}
          sx={{
            color: isDark 
              ? alpha(theme.palette.text.secondary, 0.8)
              : theme.palette.text.secondary,
            p: 1,
            '&:hover': {
              backgroundColor: isDark 
                ? alpha(theme.palette.common.white, 0.1)
                : alpha(theme.palette.text.secondary, 0.08),
              color: theme.palette.text.primary,
            },
            transition: 'all 0.2s ease',
          }}
        >
          <Icon icon={closeIcon} width={20} height={20} />
        </IconButton>
      </DialogTitle>

      <DialogContent 
        sx={{ 
          p: 0, 
          overflow: 'hidden', 
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          minHeight: 0,
        }}
      >
        <Box 
          sx={{ 
            px: 2.5,
            py: 2,
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
            minHeight: 0,
            overflow: 'auto',
          }}
        >
          <Stack spacing={2.5}>
            <Paper
              variant="outlined"
              sx={{
                p: 2,
                borderRadius: 1.25,
                bgcolor: isDark
                  ? alpha(theme.palette.background.paper, 0.4)
                  : theme.palette.background.paper,
                borderColor: isDark
                  ? alpha(theme.palette.divider, 0.12)
                  : alpha(theme.palette.divider, 0.1),
                boxShadow: isDark
                  ? `0 1px 2px ${alpha(theme.palette.common.black, 0.2)}`
                  : `0 1px 2px ${alpha(theme.palette.common.black, 0.03)}`,
              }}
            >
              <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 2 }}>
                <Box
                  sx={{
                    p: 1.25,
                    borderRadius: 1.25,
                    bgcolor: alpha(theme.palette.error.main, 0.1),
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    border: `1px solid ${alpha(theme.palette.error.main, 0.2)}`,
                    flexShrink: 0,
                  }}
                >
                  <Icon icon={warningIcon} width={20} height={20} style={{ color: theme.palette.error.main }} />
                </Box>
                <Box sx={{ flex: 1 }}>
                  <Typography variant="body2" sx={{ mb: 1, lineHeight: 1.6, fontSize: '0.875rem', fontWeight: 500 }}>
                    Are you sure you want to delete{' '}
                    <Typography
                      component="span"
                      sx={{
                        fontWeight: 700,
                        color: theme.palette.error.main,
                      }}
                    >
                      {team?.name}
                    </Typography>
                    ?
                  </Typography>
                  <Typography variant="body2" color="text.secondary" sx={{ lineHeight: 1.6, fontSize: '0.8125rem' }}>
                    This will permanently remove the team and all associated permissions. Team members will lose access to
                    resources shared with this team.
                  </Typography>
                </Box>
              </Box>
            </Paper>

            <Alert
              severity="error"
              icon={<Icon icon={warningIcon} width={20} height={20} />}
              sx={{
                borderRadius: 1.25,
                bgcolor: isDark
                  ? alpha(theme.palette.error.main, 0.15)
                  : alpha(theme.palette.error.main, 0.08),
                border: `1px solid ${alpha(theme.palette.error.main, isDark ? 0.3 : 0.2)}`,
                '& .MuiAlert-icon': {
                  color: theme.palette.error.main,
                  fontSize: '1.25rem',
                },
                '& .MuiAlert-message': {
                  width: '100%',
                },
              }}
            >
              <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.75, fontSize: '0.875rem' }}>
                Warning: Irreversible Action
              </Typography>
              <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 0.5 }}>
                <Chip
                  label={`${team?.memberCount || 0} ${team?.memberCount === 1 ? 'member' : 'members'}`}
                  size="small"
                  sx={{
                    height: 22,
                    fontSize: '0.75rem',
                    fontWeight: 600,
                    bgcolor: alpha(theme.palette.error.main, 0.15),
                    color: theme.palette.error.main,
                  }}
                />
                <Typography variant="caption" sx={{ fontSize: '0.8125rem', lineHeight: 1.5 }}>
                  will be affected by this deletion
                </Typography>
              </Stack>
            </Alert>
          </Stack>
        </Box>
      </DialogContent>

      <DialogActions
        sx={{
          px: 2.5,
          py: 2,
          borderTop: isDark
            ? `1px solid ${alpha(theme.palette.divider, 0.12)}`
            : `1px solid ${alpha(theme.palette.divider, 0.08)}`,
          flexShrink: 0,
        }}
      >
        <Box sx={{ display: 'flex', gap: 1.5, width: '100%', justifyContent: 'flex-end' }}>
          <Button
            onClick={onClose}
            disabled={submitting}
            variant="outlined"
            sx={{
              textTransform: 'none',
              fontWeight: 500,
              px: 2.5,
              py: 0.625,
              borderRadius: 1,
              fontSize: '0.8125rem',
              borderColor: isDark
                ? alpha(theme.palette.divider, 0.3)
                : alpha(theme.palette.divider, 0.2),
              color: isDark
                ? alpha(theme.palette.text.secondary, 0.9)
                : theme.palette.text.secondary,
              '&:hover': {
                borderColor: isDark
                  ? alpha(theme.palette.text.secondary, 0.5)
                  : alpha(theme.palette.text.secondary, 0.4),
                backgroundColor: isDark
                  ? alpha(theme.palette.common.white, 0.08)
                  : alpha(theme.palette.text.secondary, 0.04),
              },
              transition: 'all 0.2s ease',
            }}
          >
            Cancel
          </Button>
          <Button
            variant="contained"
            color="error"
            onClick={handleSubmit}
            disabled={submitting}
            startIcon={
              submitting ? (
                <CircularProgress size={14} color="inherit" />
              ) : (
                <Icon icon={deleteIcon} width={14} height={14} />
              )
            }
            sx={{
              textTransform: 'none',
              fontWeight: 500,
              px: 3,
              py: 0.625,
              borderRadius: 1,
              fontSize: '0.8125rem',
              boxShadow: isDark
                ? `0 2px 8px ${alpha(theme.palette.error.main, 0.3)}`
                : 'none',
              bgcolor: theme.palette.error.main,
              '&:hover': {
                bgcolor: theme.palette.error.dark,
                boxShadow: isDark
                  ? `0 4px 12px ${alpha(theme.palette.error.main, 0.4)}`
                  : `0 2px 8px ${alpha(theme.palette.error.main, 0.2)}`,
              },
              '&:active': {
                boxShadow: 'none',
              },
              '&:disabled': {
                boxShadow: 'none',
              },
              transition: 'all 0.2s ease',
            }}
          >
            {submitting ? 'Deleting...' : 'Delete Team'}
          </Button>
        </Box>
      </DialogActions>
    </Dialog>
  );
};

export default DeleteTeamDialog;
