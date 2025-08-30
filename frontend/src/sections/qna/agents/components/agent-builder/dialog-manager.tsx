// src/sections/qna/agents/components/dialog-manager.tsx
import React from 'react';
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
} from '@mui/material';
import { Icon } from '@iconify/react';
import closeIcon from '@iconify-icons/eva/close-outline';
import warningIcon from '@iconify-icons/eva/alert-triangle-outline';
import NodeConfigDialog from './node-config-dialog';
import type { AgentBuilderDialogManagerProps } from '../../types/agent';

const AgentBuilderDialogManager: React.FC<AgentBuilderDialogManagerProps> = ({
  selectedNode,
  configDialogOpen,
  onConfigDialogClose,
  onNodeConfig,
  onDeleteNode,
  deleteDialogOpen,
  onDeleteDialogClose,
  nodeToDelete,
  onConfirmDelete,
  edgeDeleteDialogOpen,
  onEdgeDeleteDialogClose,
  edgeToDelete,
  onConfirmEdgeDelete,
  deleting,
  nodes,
}) => {
  const theme = useTheme();

  // Delete Node Confirmation Dialog
  const DeleteConfirmDialog: React.FC<{
    open: boolean;
    onClose: () => void;
    onConfirm: () => Promise<void>;
    nodeId: string | null;
  }> = ({ open, onClose, onConfirm, nodeId }) => {
    const node = nodes.find((n) => n.id === nodeId);

    return (
      <Dialog
        open={open}
        onClose={onClose}
        maxWidth="sm"
        fullWidth
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
                bgcolor: alpha(theme.palette.error.main, 0.1),
                color: theme.palette.error.main,
              }}
            >
              <Icon icon={warningIcon} width={18} height={18} />
            </Box>
            Delete Node
          </Box>

          <IconButton
            onClick={onClose}
            size="small"
            sx={{ color: theme.palette.text.secondary }}
            aria-label="close"
            disabled={deleting}
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
            <Typography
              variant="body1"
              color="text.primary"
              sx={{
                lineHeight: 1.6,
                '& strong': {
                  fontWeight: 600,
                  color: theme.palette.text.primary,
                },
              }}
            >
              Are you sure you want to delete the <strong>{node?.data.label || 'selected'}</strong>{' '}
              node? This will also remove all connections to and from this node.
            </Typography>

            <Box
              sx={{
                mt: 2,
                p: 2,
                borderRadius: 1,
                bgcolor: alpha(theme.palette.error.main, 0.08),
                border: `1px solid ${alpha(theme.palette.error.main, 0.2)}`,
              }}
            >
              <Typography variant="body2" color="error.main" sx={{ fontWeight: 500 }}>
                ⚠️ This action cannot be undone
              </Typography>
            </Box>
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
            onClick={onClose}
            disabled={deleting}
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
            variant="contained"
            color="error"
            onClick={onConfirm}
            disabled={deleting}
            sx={{
              bgcolor: theme.palette.error.main,
              boxShadow: 'none',
              fontWeight: 500,
              '&:hover': {
                bgcolor: theme.palette.error.dark,
                boxShadow: 'none',
              },
              '&.Mui-disabled': {
                bgcolor: alpha(theme.palette.error.main, 0.3),
                color: alpha(theme.palette.error.contrastText, 0.5),
              },
              px: 3,
            }}
          >
            {deleting ? 'Deleting...' : 'Delete'}
          </Button>
        </DialogActions>
      </Dialog>
    );
  };

  // Edge Delete Confirmation Dialog
  const EdgeDeleteConfirmDialog: React.FC<{
    open: boolean;
    onClose: () => void;
    onConfirm: () => Promise<void>;
    edge: any;
  }> = ({ open, onClose, onConfirm, edge }) => {
    const sourceNode = nodes.find((n) => n.id === edge?.source);
    const targetNode = nodes.find((n) => n.id === edge?.target);

    return (
      <Dialog
        open={open}
        onClose={onClose}
        maxWidth="sm"
        fullWidth
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
                bgcolor: alpha(theme.palette.warning.main, 0.1),
                color: theme.palette.warning.main,
              }}
            >
              <Icon icon="mdi:connection" width={18} height={18} />
            </Box>
            Delete Connection
          </Box>

          <IconButton
            onClick={onClose}
            size="small"
            sx={{ color: theme.palette.text.secondary }}
            aria-label="close"
            disabled={deleting}
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
            <Typography
              variant="body1"
              color="text.primary"
              sx={{
                lineHeight: 1.6,
                '& strong': {
                  fontWeight: 600,
                  color: theme.palette.text.primary,
                },
              }}
            >
              Are you sure you want to delete the connection between{' '}
              <strong>{sourceNode?.data.label || edge?.source}</strong> and{' '}
              <strong>{targetNode?.data.label || edge?.target}</strong>?
            </Typography>

            <Box
              sx={{
                mt: 2,
                p: 2,
                borderRadius: 1,
                bgcolor: alpha(theme.palette.warning.main, 0.08),
                border: `1px solid ${alpha(theme.palette.warning.main, 0.2)}`,
              }}
            >
              <Typography variant="body2" color="warning.main" sx={{ fontWeight: 500 }}>
                💡 This will break the data flow between these components
              </Typography>
            </Box>
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
            onClick={onClose}
            disabled={deleting}
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
            variant="contained"
            color="warning"
            onClick={onConfirm}
            disabled={deleting}
            sx={{
              bgcolor: theme.palette.warning.main,
              boxShadow: 'none',
              fontWeight: 500,
              '&:hover': {
                bgcolor: theme.palette.warning.dark,
                boxShadow: 'none',
              },
              '&.Mui-disabled': {
                bgcolor: alpha(theme.palette.warning.main, 0.3),
                color: alpha(theme.palette.warning.contrastText, 0.5),
              },
              px: 3,
            }}
          >
            {deleting ? 'Deleting...' : 'Delete Connection'}
          </Button>
        </DialogActions>
      </Dialog>
    );
  };

  return (
    <>
      {/* Node Configuration Modal */}
      <NodeConfigDialog
        node={selectedNode}
        open={configDialogOpen}
        onClose={onConfigDialogClose}
        onSave={onNodeConfig}
        onDelete={onDeleteNode}
      />

      {/* Delete Confirmation Dialog */}
      <DeleteConfirmDialog
        open={deleteDialogOpen}
        onClose={onDeleteDialogClose}
        onConfirm={onConfirmDelete}
        nodeId={nodeToDelete}
      />

      {/* Edge Delete Confirmation Dialog */}
      <EdgeDeleteConfirmDialog
        open={edgeDeleteDialogOpen}
        onClose={onEdgeDeleteDialogClose}
        onConfirm={onConfirmEdgeDelete}
        edge={edgeToDelete}
      />


    </>
  );
};

export default AgentBuilderDialogManager;
