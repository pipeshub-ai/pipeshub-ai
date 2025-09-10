// src/sections/agents/components/template-selector.tsx
import React, { useState, useMemo } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  Box,
  Grid,
  Card,
  CardContent,
  Typography,
  Button,
  TextField,
  InputAdornment,
  Chip,
  IconButton,
  useTheme,
  alpha,
  Avatar,
  Divider,
  Menu,
  MenuItem,
  ListItemIcon,
  ListItemText,
  Paper,
  Stack,
  Tooltip,
} from '@mui/material';
import { Icon } from '@iconify/react';
import closeIcon from '@iconify-icons/mdi/close';
import searchIcon from '@iconify-icons/mdi/magnify';
import templateIcon from '@iconify-icons/mdi/file-document';
import starIcon from '@iconify-icons/mdi/star';
import accountIcon from '@iconify-icons/mdi/account';
import moreVertIcon from '@iconify-icons/mdi/dots-vertical';
import editIcon from '@iconify-icons/mdi/pencil';
import deleteIcon from '@iconify-icons/mdi/delete';
import brainIcon from '@iconify-icons/mdi/brain';
import toolIcon from '@iconify-icons/mdi/tools';
import databaseIcon from '@iconify-icons/mdi/database';
import cogIcon from '@iconify-icons/mdi/cog';
import sparklesIcon from '@iconify-icons/mdi/auto-awesome';
import type { AgentTemplate } from 'src/types/agent';
import { TEMPLATE_CATEGORIES } from '../utils/agent';
import {createScrollableContainerStyle} from '../../chatbot/utils/styles/scrollbar';

interface TemplateSelectorProps {
  open: boolean;
  onClose: () => void;
  onSelect: (template: AgentTemplate) => void;
  onEdit?: (template: AgentTemplate) => void;
  onDelete?: (template: AgentTemplate) => void;
  templates: AgentTemplate[];
}

const TemplateSelector: React.FC<TemplateSelectorProps> = ({
  open,
  onClose,
  onSelect,
  onEdit,
  onDelete,
  templates,
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';

  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<string>('');
  const [anchorEl, setAnchorEl] = useState<null | HTMLElement>(null);
  const [activeTemplate, setActiveTemplate] = useState<AgentTemplate | null>(null);
  const scrollableContainerStyle = createScrollableContainerStyle(theme);
  // Enhanced color scheme matching other components
  const bgPaper = isDark ? '#1F2937' : '#ffffff';
  const bgCard = isDark ? 'rgba(32, 30, 30, 0.5)' : '#ffffff';
  const bgHeader = isDark ? 'rgba(255, 255, 255, 0.02)' : 'rgba(0, 0, 0, 0.02)';
  const borderColor = isDark ? 'rgba(255, 255, 255, 0.08)' : 'rgba(0, 0, 0, 0.06)';
  const textPrimary = isDark ? '#ffffff' : '#1f2937';
  const textSecondary = isDark ? '#9ca3af' : '#6b7280';
  const shadowColor = isDark ? 'rgba(0, 0, 0, 0.3)' : 'rgba(0, 0, 0, 0.08)';

  // Filter templates with safe array handling
  const filteredTemplates = useMemo(() => {
    if (!Array.isArray(templates)) {
      return [];
    }

    return templates.filter((template) => {
      if (!template) return false;

      const matchesSearch =
        !searchQuery ||
        (template.name || '').toLowerCase().includes(searchQuery.toLowerCase()) ||
        (template.description || '').toLowerCase().includes(searchQuery.toLowerCase()) ||
        (Array.isArray(template.tags) &&
          template.tags.some(
            (tag) => tag && tag.toLowerCase().includes(searchQuery.toLowerCase())
          ));

      const matchesCategory = !selectedCategory || template.category === selectedCategory;

      return matchesSearch && matchesCategory;
    });
  }, [templates, searchQuery, selectedCategory]);

  // Get categories with counts - safe handling
  const categoriesWithCounts = useMemo(() => {
    if (!Array.isArray(templates)) {
      return [];
    }

    const counts = templates.reduce(
      (acc, template) => {
        if (template && template.category) {
          acc[template.category] = (acc[template.category] || 0) + 1;
        }
        return acc;
      },
      {} as Record<string, number>
    );

    return TEMPLATE_CATEGORIES.map((category) => ({
      name: category,
      count: counts[category] || 0,
    })).filter((cat) => cat.count > 0);
  }, [templates]);

  const handleTemplateSelect = (template: AgentTemplate) => {
    onSelect(template);
  };

  const handleMenuOpen = (event: React.MouseEvent<HTMLButtonElement>, template: AgentTemplate) => {
    event.stopPropagation();
    setAnchorEl(event.currentTarget);
    setActiveTemplate(template);
  };

  const handleMenuClose = () => {
    setAnchorEl(null);
    setActiveTemplate(null);
  };

  const handleEdit = () => {
    if (activeTemplate) {
      if (onEdit) {
        onEdit(activeTemplate);
      }
    }
    handleMenuClose();
  };

  const handleDelete = () => {
    if (activeTemplate) {
      if (onDelete) {
        onDelete(activeTemplate);
      }
    }
    handleMenuClose();
  };

  const handleClearSearch = () => {
    setSearchQuery('');
    setSelectedCategory('');
  };

  const renderTemplateCard = (template: AgentTemplate) => {
    if (!template || !template._key) {
      return null;
    }

    // Get template icon based on category or type
    const getTemplateIcon = (tmpl: AgentTemplate) => {
      const category = tmpl.category?.toLowerCase() || '';
      const tags = tmpl.tags || [];

      if (category.includes('support') || tags.includes('customer-support')) {
        return accountIcon;
      }
      if (category.includes('analysis') || tags.includes('data-analysis')) {
        return brainIcon;
      }
      if (category.includes('automation') || tags.includes('automation')) {
        return cogIcon;
      }
      if (category.includes('creative') || tags.includes('content-creation')) {
        return sparklesIcon;
      }

      return templateIcon;
    };

    const TemplateIcon = getTemplateIcon(template);

    return (
      <Grid item xs={12} sm={6} md={4} lg={4} key={template._key}>
        <Card
          sx={{
            height: '100%',
            minHeight: '320px',
            display: 'flex',
            flexDirection: 'column',
            borderRadius: '10px',
            bgcolor: bgCard,
            border: `1px solid ${borderColor}`,
            transition: 'all 0.2s ease-in-out',
            position: 'relative',
            overflow: 'hidden',
            cursor: 'pointer',
            '&:hover': {
              boxShadow: `0 4px 20px ${alpha(theme.palette.primary.main, 0.15)}`,
              borderColor: alpha(theme.palette.primary.main, 0.3),
              transform: 'translateY(-2px)',
            },
          }}
          onClick={() => handleTemplateSelect(template)}
        >
          {/* Header Section */}
          <Box
            sx={{
              p: 2,
              borderBottom: `1px solid ${borderColor}`,
              backgroundColor: bgHeader,
              position: 'relative',
            }}
          >
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
              <Avatar
                sx={{
                  width: 40,
                  height: 40,
                  bgcolor: alpha(theme.palette.primary.main, 0.1),
                  border: `2px solid ${alpha(theme.palette.primary.main, 0.2)}`,
                }}
              >
                <Icon
                  icon={TemplateIcon}
                  width={20}
                  height={20}
                  color={theme.palette.primary.main}
                />
              </Avatar>

              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography
                  variant="subtitle1"
                  sx={{
                    fontWeight: 600,
                    color: textPrimary,
                    fontSize: '0.875rem',
                    lineHeight: 1.2,
                    mb: 0.5,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {template.name || 'Unnamed Template'}
                </Typography>
                <Chip
                  label={template.category || 'General'}
                  size="small"
                  sx={{
                    height: 18,
                    fontSize: '0.65rem',
                    bgcolor: isDark ? 'rgba(228, 214, 214, 0.85)' : 'rgba(0, 0, 0, 0.05)',
                    color: theme.palette.primary.main,
                    border: 'none',
                    '&:hover': {
                      backgroundColor: isDark ? 'rgba(228, 214, 214, 0.85)' : 'rgba(0, 0, 0, 0.05)',
                    },
                  }}
                />
              </Box>

              {/* Menu Button */}
              <IconButton
                size="small"
                onClick={(e) => handleMenuOpen(e, template)}
                sx={{
                  width: 28,
                  height: 28,
                  borderRadius: '6px',
                  color: textSecondary,
                  '&:hover': {
                    backgroundColor: alpha(theme.palette.primary.main, 0.05),
                    color: theme.palette.primary.main,
                  },
                }}
              >
                <Icon icon={moreVertIcon} width={14} height={14} />
              </IconButton>
            </Box>
          </Box>

          {/* Content Section */}
          <CardContent
            sx={{
              p: 2,
              display: 'flex',
              flexDirection: 'column',
              flex: 1,
              gap: 1.5,
            }}
          >
            {/* Description */}
            <Typography
              variant="body2"
              sx={{
                color: textSecondary,
                fontSize: '0.75rem',
                lineHeight: 1.4,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                display: '-webkit-box',
                WebkitLineClamp: 3,
                WebkitBoxOrient: 'vertical',
                minHeight: '3.15em',
              }}
            >
              {template.description || 'No description available'}
            </Typography>

            {/* Tags */}
            {Array.isArray(template.tags) && template.tags.length > 0 && (
              <Box>
                <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                  {template.tags.slice(0, 3).map((tag, index) => (
                    <Chip
                      key={index}
                      label={tag}
                      size="small"
                      sx={{
                        height: 16,
                        fontSize: '0.6rem',
                        bgcolor: isDark ? 'rgba(228, 214, 214, 0.85)' : 'rgba(0, 0, 0, 0.05)',
                      color: theme.palette.secondary.main,
                      border: 'none',
                      '&:hover': {
                        backgroundColor: isDark ? 'rgba(228, 214, 214, 0.85)' : 'rgba(0, 0, 0, 0.05)',
                      },
                      }}
                    />
                  ))}
                  {template.tags.length > 3 && (
                    <Chip
                      label={`+${template.tags.length - 3}`}
                      size="small"
                      sx={{
                        height: 16,
                        fontSize: '0.6rem',
                        bgcolor: alpha(textSecondary, 0.1),
                        color: textSecondary,
                        border: 'none',
                      }}
                    />
                  )}
                </Box>
              </Box>
            )}

          
            {/* Stats Section */}
            <Box
              sx={{
                mt: 'auto',
                pt: 1.5,
                borderTop: `1px solid ${borderColor}`,
              }}
            >
              {/* Action Button */}
              <Button
                size="small"
                variant="outlined"
                fullWidth
                onClick={(e) => {
                  e.stopPropagation();
                  handleTemplateSelect(template);
                }}
                sx={{
                  height: 32,
                  fontSize: '0.75rem',
                  fontWeight: 500,
                  borderRadius: '6px',
                  textTransform: 'none',
                  '&:hover': {
                    backgroundColor: alpha(theme.palette.primary.main, 0.05),
                  },
                }}
              >
                Use Template
              </Button>
            </Box>
          </CardContent>
        </Card>
      </Grid>
    );
  };

  return (
    <>
      <Dialog
        open={open}
        onClose={onClose}
        maxWidth="md"
        fullWidth
        PaperProps={{
          sx: {
            borderRadius: 2,
            height: '85vh',
            display: 'flex',
            flexDirection: 'column',
            bgcolor: bgPaper,
            border: `1px solid ${borderColor}`,
          },
        }}
      >
        {/* Header */}
        <DialogTitle
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            pb: 2,
            borderBottom: `1px solid ${borderColor}`,
            bgcolor: bgHeader,
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
            <Avatar
              sx={{
                width: 32,
                height: 32,
                bgcolor: alpha(theme.palette.primary.main, 0.1),
                border: `1px solid ${alpha(theme.palette.primary.main, 0.2)}`,
              }}
            >
              <Icon icon={templateIcon} width={18} height={18} color={theme.palette.primary.main} />
            </Avatar>
            <Box>
              <Typography
                variant="h6"
                sx={{ fontWeight: 600, color: textPrimary, fontSize: '1.125rem' }}
              >
                Choose a Template
              </Typography>
              <Typography variant="caption" sx={{ color: textSecondary, fontSize: '0.75rem' }}>
                Start with a pre-built agent template
              </Typography>
            </Box>
          </Box>
          <IconButton onClick={onClose} size="small">
            <Icon icon={closeIcon} width={20} height={20} />
          </IconButton>
        </DialogTitle>

        {/* Search and Filters */}
        <Box sx={{ p: 3, pb: 2, borderBottom: `1px solid ${borderColor}` }}>
          <Stack direction="row" spacing={2} alignItems="center" sx={{ mb: 2 }}>
            <TextField
              placeholder="Search templates..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <Icon icon={searchIcon} width={20} height={20} />
                  </InputAdornment>
                ),
                endAdornment: searchQuery && (
                  <InputAdornment position="end">
                    <IconButton
                      size="small"
                      onClick={handleClearSearch}
                      sx={{
                        width: 20,
                        height: 20,
                        color: textSecondary,
                        '&:hover': {
                          color: textPrimary,
                        },
                      }}
                    >
                      <Icon icon={closeIcon} width={14} height={14} />
                    </IconButton>
                  </InputAdornment>
                ),
              }}
              size="small"
              sx={{
                flexGrow: 1,
                maxWidth: 400,
                '& .MuiOutlinedInput-root': {
                  borderColor,
                  '&:hover': {
                    borderColor: alpha(theme.palette.primary.main, 0.3),
                  },
                },
              }}
            />
            <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.875rem' }}>
              {Array.isArray(filteredTemplates) ? filteredTemplates.length : 0} template
              {(Array.isArray(filteredTemplates) ? filteredTemplates.length : 0) !== 1
                ? 's'
                : ''}{' '}
              found
            </Typography>
          </Stack>
        </Box>

        {/* Content */}
        <DialogContent sx={{ flexGrow: 1, overflow: 'auto', p: 3, ...scrollableContainerStyle }}>
          {!Array.isArray(filteredTemplates) || filteredTemplates.length === 0 ? (
            <Paper
              sx={{
                p: 6,
                textAlign: 'center',
                bgcolor: alpha(theme.palette.primary.main, 0.04),
                border: `1px solid ${alpha(theme.palette.primary.main, 0.2)}`,
                borderRadius: 2,
              }}
            >
              <Icon
                icon={templateIcon}
                width={64}
                height={64}
                color={theme.palette.text.disabled}
              />
              <Typography variant="h6" sx={{ mt: 2, mb: 1, color: textPrimary }}>
                No templates found
              </Typography>
              <Typography variant="body2" sx={{ color: textSecondary }}>
                {searchQuery || selectedCategory
                  ? 'Try adjusting your search criteria or filters'
                  : 'No templates available yet'}
              </Typography>
            </Paper>
          ) : (
            <Grid container spacing={2.5}>
              {filteredTemplates.map(renderTemplateCard).filter(Boolean)}
            </Grid>
          )}
        </DialogContent>
      </Dialog>

      {/* Context Menu */}
      <Menu
        anchorEl={anchorEl}
        open={Boolean(anchorEl)}
        onClose={handleMenuClose}
        PaperProps={{
          sx: {
            borderRadius: 2,
            minWidth: 180,
            border: `1px solid ${borderColor}`,
            bgcolor: bgPaper,
            boxShadow: `0 4px 12px ${shadowColor}`,
          },
        }}
      >
        <MenuItem
          onClick={() => {
            if (activeTemplate) handleTemplateSelect(activeTemplate);
            handleMenuClose();
          }}
        >
          <ListItemIcon>
            <Icon icon={templateIcon} width={16} height={16} />
          </ListItemIcon>
          <ListItemText>Use Template</ListItemText>
        </MenuItem>

        <MenuItem onClick={handleEdit}>
          <ListItemIcon>
            <Icon icon={editIcon} width={16} height={16} />
          </ListItemIcon>
          <ListItemText>Edit Template</ListItemText>
        </MenuItem>

        <Divider />

        <MenuItem onClick={handleDelete} sx={{ color: 'error.main' }}>
          <ListItemIcon>
            <Icon icon={deleteIcon} width={16} height={16} color={theme.palette.error.main} />
          </ListItemIcon>
          <ListItemText>Delete Template</ListItemText>
        </MenuItem>
      </Menu>
    </>
  );
};

export default TemplateSelector;
