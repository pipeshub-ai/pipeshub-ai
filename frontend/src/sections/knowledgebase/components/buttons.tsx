import { Icon } from '@iconify/react';
import React from 'react';
import pencilIcon from '@iconify-icons/mdi/pencil';
import refreshIcon from '@iconify-icons/mdi/refresh';
import trashCanIcon from '@iconify-icons/mdi/trash-can-outline';
import descriptionIcon from '@iconify-icons/mdi/file-document-outline';
import linkIcon from '@iconify-icons/mdi/open-in-new';

import {
  alpha,
  Button,
  Tooltip,
  MenuItem,
  ListItemIcon,
  ListItemText,
} from '@mui/material';

const getReindexButtonText = (status: string): string => {
  switch (status) {
    case 'FAILED':
      return 'Retry Indexing';
    case 'FILE_TYPE_NOT_SUPPORTED':
      return 'File Not Supported';
    case 'AUTO_INDEX_OFF':
      return 'Enable Indexing';
    case 'NOT_STARTED':
      return 'Start Indexing';
    case 'PAUSED':
      return 'Resume Indexing';
    case 'QUEUED':
      return 'Reindex';
    case 'EMPTY':
      return 'Retry Indexing';
    case 'ENABLE_MULTIMODAL_MODELS':
      return 'Retry Indexing';
    default:
      return 'Reindex';
  }
};

const getReindexTooltip = (status: string): string => {
  switch (status) {
    case 'FAILED':
      return 'Document indexing failed. Click to retry.';
    case 'FILE_TYPE_NOT_SUPPORTED':
      return 'This file type is not supported for indexing';
    case 'AUTO_INDEX_OFF':
      return 'Document indexing is turned off';
    case 'NOT_STARTED':
      return 'Document indexing has not started yet';
    case 'PAUSED':
      return 'Document indexing is paused. Click to resume.';
    case 'QUEUED':
      return 'Document is queued for indexing.';
    case 'IN_PROGRESS':
      return 'Document is currently being indexed';
    case 'COMPLETED':
      return 'Document has been successfully indexed. Click to reindex.';
    case 'EMPTY':
      return 'Document has no content.';
    case 'ENABLE_MULTIMODAL_MODELS':
      return 'Enable Multimodal LLM or Embedding Model to index document.';
    default:
      return 'Reindex document to update search indexes';
  }
};

// Reusable ReindexButton component to eliminate code duplication
interface ReindexButtonProps {
  recordId: string;
  indexingStatus: string;
  onRetryIndexing: (recordId: string) => void;
  variant?: 'default' | 'compact' | 'menu';
  onMenuClose?: () => void;
}

export const ReindexButton: React.FC<ReindexButtonProps> = ({
  recordId,
  indexingStatus,
  onRetryIndexing,
  variant = 'default',
  onMenuClose,
}) => {
  const isDisabled =
    indexingStatus === 'FILE_TYPE_NOT_SUPPORTED' || indexingStatus === 'IN_PROGRESS';
  const isFailed = indexingStatus === 'FAILED';

  // Get base button styles based on variant
  const getBaseStyles = () => {
    switch (variant) {
      case 'compact':
        return {
          height: 28,
          px: 1,
          py: 0.25,
          borderRadius: '6px',
          fontSize: '0.75rem',
          minWidth: 0,
          iconSize: '14px',
        };
      case 'menu':
        return null; // Menu items have different structure
      default:
        return {
          height: 32,
          px: 1.75,
          py: 0.75,
          borderRadius: '4px',
          fontSize: '0.8125rem',
          minWidth: 100,
          iconSize: '1rem',
        };
    }
  };

  // Get button text based on variant
  const getButtonText = () => {
    if (variant === 'compact') {
      return indexingStatus === 'FAILED' ? 'Retry' : 'Sync';
    }
    return getReindexButtonText(indexingStatus);
  };

  // Common button styles
  const getButtonStyles = () => {
    const base = getBaseStyles();
    if (!base) return {};

    return {
      height: base.height,
      px: base.px,
      py: base.py,
      borderRadius: base.borderRadius,
      textTransform: 'none' as const,
      fontSize: base.fontSize,
      fontWeight: 500,
      minWidth: base.minWidth,
      borderColor: (themeVal: any) =>
        isFailed
          ? themeVal.palette.mode === 'dark'
            ? '#FACC15'
            : '#D97706'
          : themeVal.palette.mode === 'dark'
            ? 'rgba(255,255,255,0.23)'
            : 'rgba(0,0,0,0.23)',
      color: (themeVal: any) =>
        isFailed
          ? themeVal.palette.mode === 'dark'
            ? '#FACC15'
            : '#D97706'
          : themeVal.palette.mode === 'dark'
            ? '#E0E0E0'
            : '#4B5563',
      borderWidth: '1px',
      bgcolor: 'transparent',
      '&:hover': {
        borderColor: (themeVal: any) =>
          isFailed
            ? themeVal.palette.mode === 'dark'
              ? '#FDE68A'
              : '#B45309'
            : themeVal.palette.mode === 'dark'
              ? 'rgba(255,255,255,0.4)'
              : 'rgba(0,0,0,0.4)',
        bgcolor: (themeVal: any) =>
          isFailed
            ? themeVal.palette.mode === 'dark'
              ? 'rgba(250,204,21,0.08)'
              : 'rgba(217,119,6,0.04)'
            : themeVal.palette.mode === 'dark'
              ? 'rgba(255,255,255,0.05)'
              : 'rgba(0,0,0,0.03)',
      },
      '&.Mui-disabled': {
        borderColor: (themeVal: any) =>
          themeVal.palette.mode === 'dark' ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.12)',
        color: (themeVal: any) =>
          themeVal.palette.mode === 'dark' ? 'rgba(255,255,255,0.3)' : 'rgba(0,0,0,0.38)',
      },
      ...(variant === 'compact' && {
        '& .MuiButton-startIcon': {
          marginRight: '4px',
          marginLeft: 0,
        },
      }),
    };
  };

  // Menu item variant
  if (variant === 'menu') {
    return (
      <MenuItem
        onClick={() => {
          onRetryIndexing(recordId);
          onMenuClose?.();
        }}
        disabled={isDisabled}
        sx={{
          py: 1,
          px: 1,
          '&:hover': {
            bgcolor: (themeVal) =>
              themeVal.palette.mode === 'dark'
                ? 'rgba(255, 255, 255, 0.05)'
                : 'rgba(0, 0, 0, 0.04)',
          },
        }}
      >
        <ListItemIcon sx={{ minWidth: 36 }}>
          <Icon
            icon={refreshIcon}
            style={{
              fontSize: '1rem',
              color: isFailed ? '#FACC15' : 'inherit',
            }}
          />
        </ListItemIcon>
        <ListItemText
          primary={getReindexButtonText(indexingStatus)}
          secondary={getReindexTooltip(indexingStatus)}
          primaryTypographyProps={{
            fontSize: '0.775rem',
            fontWeight: 500,
            color: isFailed ? '#FACC15' : 'inherit',
          }}
          secondaryTypographyProps={{
            fontSize: '0.65rem',
          }}
        />
      </MenuItem>
    );
  }

  // Button variant (default or compact)
  const base = getBaseStyles();
  const button = (
    <Button
      variant="outlined"
      startIcon={<Icon icon={refreshIcon} style={{ fontSize: base?.iconSize }} />}
      disabled={isDisabled}
      onClick={() => onRetryIndexing(recordId)}
      sx={getButtonStyles()}
    >
      {getButtonText()}
    </Button>
  );

  // Wrap in Tooltip for default variant, span for compact
  if (variant === 'compact') {
    return (
      <Tooltip title={getReindexTooltip(indexingStatus)} arrow>
        <span>{button}</span>
      </Tooltip>
    );
  }

  return (
    <Tooltip title={getReindexTooltip(indexingStatus)} placement="top" arrow>
      <span>{button}</span>
    </Tooltip>
  );
};

// Reusable EditButton component
interface EditButtonProps {
  onClick: () => void;
  variant?: 'default' | 'compact' | 'mobile';
}

export const EditButton: React.FC<EditButtonProps> = ({ onClick, variant = 'default' }) => {
  const getBaseStyles = () => {
    switch (variant) {
      case 'compact':
        return {
          height: 28,
          px: 1,
          py: 0.25,
          borderRadius: '6px',
          fontSize: '0.75rem',
          minWidth: 0,
          iconSize: '14px',
        };
      case 'mobile':
        return {
          height: 36,
          px: 1.5,
          py: 0.75,
          borderRadius: '4px',
          fontSize: '0.8125rem',
          minWidth: 0,
          iconSize: '0.875rem',
          flex: 1,
        };
      default:
        return {
          height: 32,
          px: 1.75,
          py: 0.75,
          borderRadius: '4px',
          fontSize: '0.8125rem',
          minWidth: 100,
          iconSize: '1rem',
        };
    }
  };

  const base = getBaseStyles();

  return (
    <Button
      variant="outlined"
      startIcon={<Icon icon={pencilIcon} style={{ fontSize: base.iconSize }} />}
      onClick={onClick}
      sx={{
        height: base.height,
        px: base.px,
        py: base.py,
        borderRadius: base.borderRadius,
        textTransform: 'none',
        fontSize: base.fontSize,
        fontWeight: 500,
        minWidth: base.minWidth,
        ...(base.flex && { flex: base.flex }),
        borderColor: (themeVal) =>
          themeVal.palette.mode === 'dark'
            ? alpha(themeVal.palette.primary.main, 0.7)
            : themeVal.palette.primary.main,
        color: (themeVal) =>
          themeVal.palette.mode === 'dark'
            ? themeVal.palette.primary.light
            : themeVal.palette.primary.main,
        borderWidth: '1px',
        bgcolor: 'transparent',
        '&:hover': {
          borderColor: (themeVal) =>
            themeVal.palette.mode === 'dark'
              ? themeVal.palette.primary.light
              : themeVal.palette.primary.dark,
          bgcolor: (themeVal) =>
            themeVal.palette.mode === 'dark'
              ? alpha(themeVal.palette.primary.main, 0.1)
              : alpha(themeVal.palette.primary.main, 0.05),
        },
        ...(variant === 'compact' && {
          '& .MuiButton-startIcon': {
            marginRight: '4px',
            marginLeft: 0,
          },
        }),
      }}
    >
      Edit
    </Button>
  );
};

// Reusable SummaryButton component
interface SummaryButtonProps {
  onClick: () => void;
  variant?: 'default' | 'compact' | 'menu';
  onMenuClose?: () => void;
}

export const SummaryButton: React.FC<SummaryButtonProps> = ({
  onClick,
  variant = 'default',
  onMenuClose,
}) => {
  const getBaseStyles = () => {
    switch (variant) {
      case 'compact':
        return {
          height: 28,
          px: 1,
          py: 0.25,
          borderRadius: '6px',
          fontSize: '0.75rem',
          minWidth: 0,
          iconSize: '14px',
        };
      case 'menu':
        return null;
      default:
        return {
          height: 32,
          px: 1.75,
          py: 0.75,
          borderRadius: '4px',
          fontSize: '0.8125rem',
          minWidth: 100,
          iconSize: '1rem',
        };
    }
  };

  if (variant === 'menu') {
    return (
      <MenuItem
        onClick={() => {
          onClick();
          onMenuClose?.();
        }}
        sx={{
          py: 1,
          px: 1,
          '&:hover': {
            bgcolor: (themeVal) =>
              themeVal.palette.mode === 'dark'
                ? 'rgba(255, 255, 255, 0.05)'
                : 'rgba(0, 0, 0, 0.04)',
          },
        }}
      >
        <ListItemIcon sx={{ minWidth: 36 }}>
          <Icon icon={descriptionIcon} style={{ fontSize: '1.125rem' }} />
        </ListItemIcon>
        <ListItemText
          primary="View Summary"
          secondary="Show document summary"
          primaryTypographyProps={{
            fontSize: '0.775rem',
            fontWeight: 500,
          }}
          secondaryTypographyProps={{
            fontSize: '0.65rem',
          }}
        />
      </MenuItem>
    );
  }

  const base = getBaseStyles();

  return (
    <Button
      variant="outlined"
      startIcon={<Icon icon={descriptionIcon} style={{ fontSize: base?.iconSize }} />}
      onClick={onClick}
      sx={{
        height: base?.height,
        px: base?.px,
        py: base?.py,
        borderRadius: base?.borderRadius,
        textTransform: 'none',
        fontSize: base?.fontSize,
        fontWeight: 500,
        minWidth: base?.minWidth,
        borderColor: (themeVal) =>
          themeVal.palette.mode === 'dark' ? 'rgba(255,255,255,0.23)' : 'rgba(0,0,0,0.23)',
        color: (themeVal) => (themeVal.palette.mode === 'dark' ? '#E0E0E0' : '#4B5563'),
        borderWidth: '1px',
        bgcolor: 'transparent',
        '&:hover': {
          borderColor: (themeVal) =>
            themeVal.palette.mode === 'dark' ? 'rgba(255,255,255,0.4)' : 'rgba(0,0,0,0.4)',
          bgcolor: (themeVal) =>
            themeVal.palette.mode === 'dark' ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.03)',
        },
        ...(variant === 'compact' && {
          '& .MuiButton-startIcon': {
            marginRight: '4px',
            marginLeft: 0,
          },
        }),
      }}
    >
      Summary
    </Button>
  );
};

// Reusable OpenButton component
interface OpenButtonProps {
  webUrl: string;
  variant?: 'default' | 'compact' | 'menu';
  onMenuClose?: () => void;
}

export const OpenButton: React.FC<OpenButtonProps> = ({
  webUrl,
  variant = 'default',
  onMenuClose,
}) => {
  const getBaseStyles = () => {
    switch (variant) {
      case 'compact':
        return {
          height: 28,
          px: 1,
          py: 0.25,
          borderRadius: '6px',
          fontSize: '0.75rem',
          minWidth: 0,
          iconSize: '14px',
        };
      case 'menu':
        return null;
      default:
        return {
          height: 32,
          px: 1.75,
          py: 0.75,
          borderRadius: '4px',
          fontSize: '0.8125rem',
          minWidth: 100,
          iconSize: '1rem',
        };
    }
  };

  const handleClick = () => {
    window.open(webUrl, '_blank', 'noopener,noreferrer');
    onMenuClose?.();
  };

  if (variant === 'menu') {
    return (
      <MenuItem
        onClick={handleClick}
        sx={{
          py: 1,
          px: 1,
          '&:hover': {
            bgcolor: (themeVal) =>
              themeVal.palette.mode === 'dark'
                ? 'rgba(255, 255, 255, 0.05)'
                : 'rgba(0, 0, 0, 0.04)',
          },
        }}
      >
        <ListItemIcon sx={{ minWidth: 36 }}>
          <Icon icon={linkIcon} style={{ fontSize: '1.125rem' }} />
        </ListItemIcon>
        <ListItemText
          primary="Open Link"
          secondary="Open in new tab"
          primaryTypographyProps={{
            fontSize: '0.775rem',
            fontWeight: 500,
          }}
          secondaryTypographyProps={{
            fontSize: '0.65rem',
          }}
        />
      </MenuItem>
    );
  }

  const base = getBaseStyles();

  return (
    <Button
      variant="outlined"
      startIcon={<Icon icon={linkIcon} style={{ fontSize: base?.iconSize }} />}
      onClick={handleClick}
      sx={{
        height: base?.height,
        px: base?.px,
        py: base?.py,
        borderRadius: base?.borderRadius,
        textTransform: 'none',
        fontSize: base?.fontSize,
        fontWeight: 500,
        minWidth: base?.minWidth,
        borderColor: (themeVal) =>
          themeVal.palette.mode === 'dark' ? 'rgba(255,255,255,0.23)' : 'rgba(0,0,0,0.23)',
        color: (themeVal) => (themeVal.palette.mode === 'dark' ? '#E0E0E0' : '#4B5563'),
        borderWidth: '1px',
        bgcolor: 'transparent',
        '&:hover': {
          borderColor: (themeVal) =>
            themeVal.palette.mode === 'dark' ? 'rgba(255,255,255,0.4)' : 'rgba(0,0,0,0.4)',
          bgcolor: (themeVal) =>
            themeVal.palette.mode === 'dark' ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.03)',
        },
        ...(variant === 'compact' && {
          '& .MuiButton-startIcon': {
            marginRight: '4px',
            marginLeft: 0,
          },
        }),
      }}
    >
      Open
    </Button>
  );
};

// Reusable DeleteButton component
interface DeleteButtonProps {
  onClick: () => void;
  variant?: 'default' | 'compact' | 'menu';
  onMenuClose?: () => void;
}

export const DeleteButton: React.FC<DeleteButtonProps> = ({
  onClick,
  variant = 'default',
  onMenuClose,
}) => {
  const getBaseStyles = () => {
    switch (variant) {
      case 'compact':
        return {
          height: 28,
          px: 1,
          py: 0.25,
          borderRadius: '6px',
          fontSize: '0.75rem',
          minWidth: 0,
          iconSize: '14px',
        };
      case 'menu':
        return null;
      default:
        return {
          height: 32,
          px: 1.75,
          py: 0.75,
          borderRadius: '4px',
          fontSize: '0.8125rem',
          minWidth: 100,
          iconSize: '1rem',
        };
    }
  };

  if (variant === 'menu') {
    return (
      <MenuItem
        onClick={() => {
          onClick();
          onMenuClose?.();
        }}
        sx={{
          py: 1,
          px: 1,
          color: '#DC2626',
          '&:hover': {
            bgcolor: 'rgba(220, 38, 38, 0.04)',
          },
        }}
      >
        <ListItemIcon sx={{ minWidth: 36 }}>
          <Icon
            icon={trashCanIcon}
            style={{
              fontSize: '1.125rem',
              color: '#DC2626',
            }}
          />
        </ListItemIcon>
        <ListItemText
          primary="Delete Record"
          secondary="Permanently remove this record"
          primaryTypographyProps={{
            fontSize: '0.775rem',
            fontWeight: 500,
          }}
          secondaryTypographyProps={{
            fontSize: '0.65rem',
          }}
        />
      </MenuItem>
    );
  }

  const base = getBaseStyles();

  return (
    <Button
      variant="outlined"
      color="error"
      startIcon={<Icon icon={trashCanIcon} style={{ fontSize: base?.iconSize }} />}
      onClick={onClick}
      sx={{
        height: base?.height,
        px: base?.px,
        py: base?.py,
        borderRadius: base?.borderRadius,
        textTransform: 'none',
        fontSize: base?.fontSize,
        fontWeight: 500,
        minWidth: base?.minWidth,
        borderColor: (themeVal) => (themeVal.palette.mode === 'dark' ? '#EF4444' : '#DC2626'),
        color: (themeVal) => (themeVal.palette.mode === 'dark' ? '#EF4444' : '#DC2626'),
        borderWidth: '1px',
        bgcolor: 'transparent',
        '&:hover': {
          borderColor: (themeVal) => (themeVal.palette.mode === 'dark' ? '#F87171' : '#B91C1C'),
          bgcolor: (themeVal) =>
            themeVal.palette.mode === 'dark' ? 'rgba(239,68,68,0.08)' : 'rgba(220,38,38,0.04)',
        },
        ...(variant === 'compact' && {
          '& .MuiButton-startIcon': {
            marginRight: '4px',
            marginLeft: 0,
          },
        }),
      }}
    >
      Delete
    </Button>
  );
};
