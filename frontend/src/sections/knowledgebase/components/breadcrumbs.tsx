import { Icon } from '@iconify/react';
import {
  Breadcrumbs,
  Typography,
  Stack,
  Box,
  Menu,
  MenuItem,
  ListItemIcon,
  ListItemText,
  styled,
} from '@mui/material';
import { useState } from 'react';
import chevronRightIcon from '@iconify-icons/mdi/chevron-right';
import homeIcon from '@iconify-icons/mdi/home';
import brainIcon from '@iconify-icons/mdi/brain';
import folderIcon from '@iconify-icons/mdi/folder';

export const renderSmartBreadcrumbs = (
  navigationPath: any,
  navigateToDashboard: any,
  navigateToPathIndex: any,
  theme: any
) => {
  const maxVisibleItems = 4;
  const pathLength = navigationPath.length;

  if (pathLength <= maxVisibleItems) {
    return (
      <Breadcrumbs
        separator={
          <Icon icon={chevronRightIcon} fontSize={14} color={theme.palette.text.disabled} />
        }
        sx={{
          '& .MuiBreadcrumbs-separator': {
            mx: 0.5,
          },
          '& .MuiBreadcrumbs-ol': {
            flexWrap: 'nowrap',
            overflow: 'hidden',
          },
        }}
      >
        <CompactBreadcrumb component="button" onClick={navigateToDashboard} isHome>
          <Icon icon={homeIcon} fontSize={14} />
          <Typography
            sx={{ display: { xs: 'none', sm: 'inline' }, ml: 0.5, fontSize: '0.8125rem' }}
          >
            Home
          </Typography>
        </CompactBreadcrumb>
        {navigationPath.map((item: any, index: any) => (
          <CompactBreadcrumb
            key={item.id}
            component="button"
            onClick={() => navigateToPathIndex(index)}
            isLast={index === pathLength - 1}
          >
            <Icon icon={item.type === 'kb' ? brainIcon : folderIcon} fontSize={14} />
            <Typography
              sx={{
                ml: 0.5,
                fontSize: '0.8125rem',
                maxWidth: { xs: 80, sm: 120 },
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {item.name}
            </Typography>
          </CompactBreadcrumb>
        ))}
      </Breadcrumbs>
    );
  }

  const currentItem = navigationPath[pathLength - 1];
  const parentItem = pathLength > 1 ? navigationPath[pathLength - 2] : null;
  const kbItem = navigationPath[0];

  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.5}
      sx={{ minWidth: 0, overflow: 'hidden' }}
    >
      <CompactBreadcrumb component="button" onClick={navigateToDashboard} isHome>
        <Icon icon={homeIcon} fontSize={14} />
        <Typography sx={{ display: { xs: 'none', sm: 'inline' }, ml: 0.5, fontSize: '0.8125rem' }}>
          Home
        </Typography>
      </CompactBreadcrumb>

      <Icon icon={chevronRightIcon} fontSize={14} color={theme.palette.text.disabled} />

      <CompactBreadcrumb component="button" onClick={() => navigateToPathIndex(0)}>
        <Icon icon={brainIcon} fontSize={14} />
        <Typography
          sx={{
            ml: 0.5,
            fontSize: '0.8125rem',
            maxWidth: { xs: 60, sm: 100 },
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {kbItem.name}
        </Typography>
      </CompactBreadcrumb>

      {pathLength > 2 && (
        <>
          <Icon icon={chevronRightIcon} fontSize={14} color={theme.palette.text.disabled} />
          <SimpleBreadcrumbDropdown
            hiddenItems={navigationPath.slice(1, pathLength - 1)}
            onItemClick={navigateToPathIndex}
          />
        </>
      )}

      {parentItem && pathLength > 1 && (
        <>
          <Icon icon={chevronRightIcon} fontSize={14} color={theme.palette.text.disabled} />
          <CompactBreadcrumb component="button" onClick={() => navigateToPathIndex(pathLength - 2)}>
            <Icon icon={folderIcon} fontSize={14} />
            <Typography
              sx={{
                ml: 0.5,
                fontSize: '0.8125rem',
                maxWidth: { xs: 50, sm: 80 },
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {parentItem.name}
            </Typography>
          </CompactBreadcrumb>
        </>
      )}

      <Icon icon={chevronRightIcon} fontSize={14} color={theme.palette.text.disabled} />
      <CompactBreadcrumb component="span" isLast>
        <Icon icon={folderIcon} fontSize={14} />
        <Typography
          sx={{
            ml: 0.5,
            fontSize: '0.8125rem',
            maxWidth: { xs: 60, sm: 100 },
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {currentItem.name}
        </Typography>
      </CompactBreadcrumb>
    </Stack>
  );
};

const CompactBreadcrumb = styled('div')<{
  isHome?: boolean;
  isLast?: boolean;
  component?: string;
}>(({ theme: muiTheme, isHome, isLast }) => ({
  display: 'flex',
  alignItems: 'center',
  padding: '4px 6px',
  borderRadius: 4,
  fontSize: '0.8125rem',
  fontWeight: 500,
  color: isLast ? muiTheme.palette.text.primary : muiTheme.palette.text.secondary,
  backgroundColor: 'transparent',
  border: 'none',
  cursor: isLast ? 'default' : 'pointer',
  transition: 'all 0.2s ease',
  minWidth: 0,

  ...(!isLast && {
    '&:hover': {
      backgroundColor: muiTheme.palette.action.hover,
      color: muiTheme.palette.text.primary,
    },
  }),
}));

const SimpleBreadcrumbDropdown = ({
  hiddenItems,
  onItemClick,
}: {
  hiddenItems: Array<{ id: string; name: string; type: 'kb' | 'folder' }>;
  onItemClick: (index: number) => void;
}) => {
  const [dropdownAnchor, setDropdownAnchor] = useState<null | HTMLElement>(null);

  return (
    <>
      <Box
        component="button"
        onClick={(e) => setDropdownAnchor(e.currentTarget)}
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          minWidth: 24,
          height: 24,
          borderRadius: 0.5,
          backgroundColor: 'transparent',
          border: 'none',
          color: 'text.secondary',
          cursor: 'pointer',
          fontSize: '0.8125rem',
          fontWeight: 600,
          '&:hover': {
            backgroundColor: 'action.hover',
            color: 'text.primary',
          },
        }}
      >
        ...
      </Box>

      <Menu
        anchorEl={dropdownAnchor}
        open={Boolean(dropdownAnchor)}
        onClose={() => setDropdownAnchor(null)}
        PaperProps={{
          sx: {
            borderRadius: 1.5,
            minWidth: 180,
            border: (themeVal) => `1px solid ${themeVal.palette.divider}`,
            boxShadow: (themeVal) => themeVal.shadows[3],
            mt: 0.5,
          },
        }}
      >
        {hiddenItems.map((item: any, relativeIndex: any) => {
          const actualIndex = relativeIndex + 1;
          return (
            <MenuItem
              key={item.id}
              onClick={() => {
                onItemClick(actualIndex);
                setDropdownAnchor(null);
              }}
              sx={{
                fontSize: '0.8125rem',
                py: 1,
                px: 1.5,
                minHeight: 'auto',
              }}
            >
              <ListItemIcon sx={{ minWidth: 28 }}>
                <Icon icon={item.type === 'kb' ? brainIcon : folderIcon} fontSize={16} />
              </ListItemIcon>
              <ListItemText
                primary={item.name}
                sx={{
                  '& .MuiListItemText-primary': {
                    fontSize: '0.8125rem',
                    fontWeight: 500,
                  },
                }}
              />
            </MenuItem>
          );
        })}
      </Menu>
    </>
  );
};
