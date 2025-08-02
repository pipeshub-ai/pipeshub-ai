// Memoized Menu Button Component with Fixed Positioning
import React, { memo, useState, useCallback } from 'react';
import { Icon } from '@iconify/react';
import { Theme, alpha } from '@mui/material/styles';
import { Popover, ClickAwayListener, MenuList, MenuItem, ListItemIcon, ListItemText, IconButton } from '@mui/material';
import moreVertIcon from '@iconify-icons/mdi/dots-vertical';
import editIcon from '@iconify-icons/mdi/pencil-outline';
import deleteIcon from '@iconify-icons/mdi/delete-outline';
import { KnowledgeBase } from '../types/kb';


// Styled components
const CompactIconButton = memo(({ theme, ...props }: any) => (
    <IconButton
      sx={{
        width: 32,
        height: 32,
        borderRadius: 1,
        color: 'action.active',
        backgroundColor: 'transparent',
        border: (themeVal) => `1px solid ${themeVal.palette.divider}`,
        '&:hover': {
          backgroundColor: 'action.hover',
          borderColor: 'action.active',
        },
      }}
      {...props}
    />
  ));


export const MenuButton = memo<{
    kb: KnowledgeBase;
    onEdit: (kb: KnowledgeBase) => void;
    onDelete: (kb: KnowledgeBase) => void;
    theme: any;
    className?: string;
    sx?: any;
  }>(({ kb, onEdit, onDelete, theme, className, sx }) => {
    const [open, setOpen] = useState(false);
    const [anchorEl, setAnchorEl] = useState<null | HTMLElement>(null);
  
    const handleToggle = useCallback((event: React.MouseEvent<HTMLButtonElement>) => {
      event.stopPropagation();
      event.preventDefault();
      setAnchorEl(event.currentTarget);
      setOpen((prevOpen) => !prevOpen);
    }, []);
  
    const handleClose = useCallback(
      (event?: Event | React.SyntheticEvent | {}, reason?: 'backdropClick' | 'escapeKeyDown') => {
        // Only check for contains if event is an actual Event or SyntheticEvent
        if (
          event &&
          'target' in event &&
          anchorEl &&
          anchorEl.contains(event.target as HTMLElement)
        ) {
          return;
        }
        setOpen(false);
        setAnchorEl(null);
      },
      [anchorEl]
    );
  
    const handleEdit = useCallback(
      (event: React.MouseEvent) => {
        event.stopPropagation();
        onEdit(kb);
        handleClose();
      },
      [onEdit, kb, handleClose]
    );
  
    const handleDelete = useCallback(
      (event: React.MouseEvent) => {
        event.stopPropagation();
        onDelete(kb);
        handleClose();
      },
      [onDelete, kb, handleClose]
    );
  
    return (
      <>
        <CompactIconButton
          size="small"
          onClick={handleToggle}
          className={className}
          sx={{
            ...sx,
            width: 28,
            height: 28,
            borderRadius: '4px',
            opacity: 0.7,
            transition: 'all 0.2s ease',
            '&:hover': {
              opacity: 1,
              backgroundColor: (themeVal: Theme) => alpha(themeVal.palette.grey[500], 0.1),
              borderColor: (themeVal: Theme) => alpha(themeVal.palette.grey[500], 0.3),
            },
          }}
          theme={theme}
          aria-controls={open ? 'composition-menu' : undefined}
          aria-expanded={open ? 'true' : undefined}
          aria-haspopup="true"
        >
          <Icon icon={moreVertIcon} fontSize={14} />
        </CompactIconButton>
  
        <Popover
          open={open}
          anchorEl={anchorEl}
          onClose={handleClose}
          anchorOrigin={{
            vertical: 'bottom',
            horizontal: 'right',
          }}
          transformOrigin={{
            vertical: 'top',
            horizontal: 'right',
          }}
          PaperProps={{
            sx: {
              borderRadius: 2,
              minWidth: 140,
              backgroundColor: 'background.paper',
              border: (themeVal) => `1px solid ${themeVal.palette.divider}`,
              boxShadow: (themeVal) => themeVal.shadows[8],
              overflow: 'hidden',
              mt: 0.5,
            },
          }}
          slotProps={{
            paper: {
              elevation: 0,
            },
          }}
        >
          <ClickAwayListener onClickAway={handleClose}>
            <MenuList autoFocusItem={open} id="composition-menu" sx={{ p: 0.5 }}>
              <MenuItem
                onClick={handleEdit}
                sx={{
                  fontSize: '0.8125rem',
                  py: 1,
                  px: 1.5,
                  borderRadius: 1,
                  mx: 0.5,
                  '&:hover': {
                    backgroundColor: 'action.hover',
                  },
                }}
              >
                <ListItemIcon sx={{ minWidth: 28 }}>
                  <Icon icon={editIcon} fontSize={16} />
                </ListItemIcon>
                <ListItemText
                  primary="Edit"
                  primaryTypographyProps={{
                    fontSize: '0.8125rem',
                    fontWeight: 500,
                  }}
                />
              </MenuItem>
              <MenuItem
                onClick={handleDelete}
                sx={{
                  color: 'error.main',
                  fontSize: '0.8125rem',
                  py: 1,
                  px: 1.5,
                  borderRadius: 1,
                  mx: 0.5,
                  '&:hover': {
                    backgroundColor: (themeVal) => alpha(themeVal.palette.error.main, 0.08),
                  },
                }}
              >
                <ListItemIcon sx={{ minWidth: 28 }}>
                  <Icon icon={deleteIcon} fontSize={16} color={theme.palette.error.main} />
                </ListItemIcon>
                <ListItemText
                  primary="Delete"
                  primaryTypographyProps={{
                    fontSize: '0.8125rem',
                    fontWeight: 500,
                  }}
                />
              </MenuItem>
            </MenuList>
          </ClickAwayListener>
        </Popover>
      </>
    );
  });
  
  MenuButton.displayName = 'MenuButton';
  
  