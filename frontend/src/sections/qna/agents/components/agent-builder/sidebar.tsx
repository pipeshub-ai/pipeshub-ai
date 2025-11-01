// src/sections/agents/components/flow-builder-sidebar.tsx
import React, { useState, useMemo } from 'react';
import {
  Box,
  Typography,
  IconButton,
  Drawer,
  List,
  ListItem,
  TextField,
  Collapse,
  useTheme,
  alpha,
  CircularProgress,
  InputAdornment,
} from '@mui/material';
import { Icon } from '@iconify/react';

// Basic UI icons
import chevronDownIcon from '@iconify-icons/mdi/chevron-down';
import chevronRightIcon from '@iconify-icons/mdi/chevron-right';
import searchIcon from '@iconify-icons/mdi/magnify';
import clearIcon from '@iconify-icons/mdi/close';
import inputOutputIcon from '@iconify-icons/mdi/swap-horizontal';

// Category icons
import agentIcon from '@iconify-icons/mdi/robot-outline';
import modelIcon from '@iconify-icons/mdi/chip';
import dataIcon from '@iconify-icons/mdi/database-outline';
import vectorIcon from '@iconify-icons/mdi/vector-triangle';
import processingIcon from '@iconify-icons/mdi/lightning-bolt';
import bundleIcon from '@iconify-icons/mdi/package-variant';
import cloudIcon from '@iconify-icons/mdi/cloud-outline';
import applicationIcon from '@iconify-icons/mdi/application';

// Communication & Email icons
import replyIcon from '@iconify-icons/mdi/reply';
import emailSendIcon from '@iconify-icons/mdi/email-send';
import emailEditIcon from '@iconify-icons/mdi/email-edit';
import emailSearchIcon from '@iconify-icons/mdi/email-search';
import emailOpenIcon from '@iconify-icons/mdi/email-open';
import emailIcon from '@iconify-icons/mdi/email';
import paperclipIcon from '@iconify-icons/mdi/paperclip';
import sendIcon from '@iconify-icons/mdi/send';
import sendOutlineIcon from '@iconify-icons/mdi/send-outline';

// Calendar icons
import calendarPlusIcon from '@iconify-icons/mdi/calendar-plus';
import calendarEditIcon from '@iconify-icons/mdi/calendar-edit';
import calendarRemoveIcon from '@iconify-icons/mdi/calendar-remove';
import calendarSearchIcon from '@iconify-icons/mdi/calendar-search';
import calendarClockIcon from '@iconify-icons/mdi/calendar-clock';
import calendarIcon from '@iconify-icons/mdi/calendar';

// CRUD operation icons
import plusCircleOutlineIcon from '@iconify-icons/mdi/plus-circle-outline';
import pencilOutlineIcon from '@iconify-icons/mdi/pencil-outline';
import deleteOutlineIcon from '@iconify-icons/mdi/delete-outline';
import downloadOutlineIcon from '@iconify-icons/mdi/download-outline';
import uploadIcon from '@iconify-icons/mdi/upload';
import downloadIcon from '@iconify-icons/mdi/download';

// File & folder icons
import folderPlusOutlineIcon from '@iconify-icons/mdi/folder-plus-outline';
import folderOutlineIcon from '@iconify-icons/mdi/folder-outline';
import folderMultipleOutlineIcon from '@iconify-icons/mdi/folder-multiple-outline';
import fileDocumentOutlineIcon from '@iconify-icons/mdi/file-document-outline';
import fileDocumentMultipleOutlineIcon from '@iconify-icons/mdi/file-document-multiple-outline';
import shareVariantOutlineIcon from '@iconify-icons/mdi/share-variant-outline';

// Development & code icons
import sourceRepositoryIcon from '@iconify-icons/mdi/source-repository';
import bugOutlineIcon from '@iconify-icons/mdi/bug-outline';
import sourcePullIcon from '@iconify-icons/mdi/source-pull';
import sourceCommitIcon from '@iconify-icons/mdi/source-commit';
import sourceBranchIcon from '@iconify-icons/mdi/source-branch';

// Communication & team icons
import commentOutlineIcon from '@iconify-icons/mdi/comment-outline';
import accountOutlineIcon from '@iconify-icons/mdi/account-outline';
import accountPlusOutlineIcon from '@iconify-icons/mdi/account-plus-outline';
import poundBoxOutlineIcon from '@iconify-icons/mdi/pound-box-outline';

// Utility icons
import formatListBulletIcon from '@iconify-icons/mdi/format-list-bulleted';
import arrowRightCircleOutlineIcon from '@iconify-icons/mdi/arrow-right-circle-outline';
import closeCircleOutlineIcon from '@iconify-icons/mdi/close-circle-outline';
import minusCircleOutlineIcon from '@iconify-icons/mdi/minus-circle-outline';

// Math & calculation icons
import divisionIcon from '@iconify-icons/mdi/division';
import equalIcon from '@iconify-icons/mdi/equal';

// Brand/app specific icons
import googleGmailIcon from '@iconify-icons/logos/google-gmail';
import googleCalendarIcon from '@iconify-icons/logos/google-calendar';
import googleDriveIcon from '@iconify-icons/logos/google-drive';
import googleWorkspaceIcon from '@iconify-icons/logos/google-icon';
import microsoftOnedriveIcon from '@iconify-icons/logos/microsoft-onedrive';
import confluenceIcon from '@iconify-icons/logos/confluence';
import githubIcon from '@iconify-icons/mdi/github';
import jiraIcon from '@iconify-icons/logos/jira';
import slackIcon from '@iconify-icons/logos/slack-icon';
import cogOutlineIcon from '@iconify-icons/mdi/cog-outline';
import calculatorIcon from '@iconify-icons/mdi/calculator';
import googleDocsIcon from '@iconify-icons/logos/google';
import googleMeetIcon from '@iconify-icons/logos/google-meet';
import notionIcon from '@iconify-icons/logos/notion';
import { useConnectors } from '../../../../accountdetails/connectors/context';

// Utility functions
import { normalizeDisplayName } from '../../utils/agent';

interface NodeTemplate {
  type: string;
  label: string;
  description: string;
  icon: any;
  defaultConfig: Record<string, any>;
  inputs: string[];
  outputs: string[];
  category: 'inputs' | 'llm' | 'tools' | 'knowledge' | 'outputs' | 'agent';
}

interface FlowBuilderSidebarProps {
  sidebarOpen: boolean;
  nodeTemplates: NodeTemplate[];
  loading: boolean;
  sidebarWidth: number;
}

const FlowBuilderSidebar: React.FC<FlowBuilderSidebarProps> = ({
  sidebarOpen,
  nodeTemplates,
  loading,
  sidebarWidth,
}) => {
  const theme = useTheme();
  const [searchQuery, setSearchQuery] = useState('');
  const [expandedCategories, setExpandedCategories] = useState<Record<string, boolean>>({
    'Input / Output': true,
    Agents: false,
    'LLM Models': false,
    Knowledge: false,
    Tools: true,
    'Vector Stores': false,
  });
  const [expandedApps, setExpandedApps] = useState<Record<string, boolean>>({});

  // Get connector data from the hook
  const { activeConnectors } = useConnectors();
  const allConnectors = [...activeConnectors];

  // Filter templates based on search query
  const filteredTemplates = useMemo(() => {
    if (!searchQuery.trim()) return nodeTemplates;

    const query = searchQuery.toLowerCase();
    return nodeTemplates.filter(
      (template) =>
        template.label.toLowerCase().includes(query) ||
        template.description.toLowerCase().includes(query) ||
        template.category.toLowerCase().includes(query) ||
        (template.defaultConfig?.appName &&
          template.defaultConfig.appName.toLowerCase().includes(query))
    );
  }, [nodeTemplates, searchQuery]);

  // Normalize app names for better display
  const normalizeAppName = (appName: string): string => {
    const nameMap: Record<string, string> = {
      calculator: 'Calculator',
      gmail: 'Gmail',
      google_calendar: 'Google Calendar',
      google_drive: 'Google Drive',
      confluence: 'Confluence',
      github: 'GitHub',
      jira: 'Jira',
      slack: 'Slack',
      google_drive_enterprise: 'Google Drive Enterprise',
      calendar: 'Calendar',
    };

    return (
      nameMap[appName] ||
      appName
        .split('_')
        .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
        .join(' ')
    );
  };

  // Get app-level tool nodes (tool-group-* nodes)
  const appToolNodes = useMemo(
    () =>
      filteredTemplates.filter((t) => t.category === 'tools' && t.type.startsWith('tool-group-')),
    [filteredTemplates]
  );

  // Group individual tools by app name for dropdown
  const groupedByApp = useMemo(() => {
    const individualTools = filteredTemplates.filter(
      (t) =>
        t.category === 'tools' && t.type.startsWith('tool-') && !t.type.startsWith('tool-group-')
    );
    const grouped: Record<string, NodeTemplate[]> = {};

    individualTools.forEach((template) => {
      const appName = template.defaultConfig?.appName || 'Other';
      const displayName = normalizeAppName(appName);

      if (!grouped[displayName]) {
        grouped[displayName] = [];
      }
      grouped[displayName].push(template);
    });

    return grouped;
  }, [filteredTemplates]);

  // Get memory-related nodes for Memory section
  const kbGroupNode = useMemo(
    () => filteredTemplates.find((t) => t.type === 'kb-group'),
    [filteredTemplates]
  );

  const appKnowledgeGroupNode = useMemo(
    () => filteredTemplates.find((t) => t.type === 'app-group'),
    [filteredTemplates]
  );

  const individualKBs = useMemo(
    () =>
      filteredTemplates.filter(
        (t) => t.category === 'knowledge' && t.type.startsWith('kb-') && t.type !== 'kb-group'
      ),
    [filteredTemplates]
  );

  const individualAppKnowledge = useMemo(
    () =>
      filteredTemplates.filter(
        (t) => t.category === 'knowledge' && t.type.startsWith('app-') && t.type !== 'app-group'
      ),
    [filteredTemplates]
  );

  const handleCategoryToggle = (categoryName: string) => {
    setExpandedCategories((prev) => ({
      ...prev,
      [categoryName]: !prev[categoryName],
    }));
  };

  const handleAppToggle = (appName: string) => {
    setExpandedApps((prev) => ({
      ...prev,
      [appName]: !prev[appName],
    }));
  };

  // Unified component rendering for consistency
  const renderDraggableItem = (
    template: NodeTemplate,
    isSubItem = false,
    sectionType?: 'tools' | 'apps' | 'kbs'
  ) => {
    // Get the appropriate icon based on the item type
    let itemIcon = template.icon;
    let isDynamicIcon = false;

    if (sectionType === 'apps' && template.defaultConfig?.appName) {
      const appIcon = getAppKnowledgeIcon(template.defaultConfig.appName);
      if (appIcon === 'dynamic-icon') {
        isDynamicIcon = true;
        // Find the connector for dynamic icon
        const connector = allConnectors.find(
          (c) =>
            c.name.toUpperCase() === template.defaultConfig.appName.toUpperCase() ||
            c.name === template.defaultConfig.appName
        );
        itemIcon = connector?.iconPath || '/assets/icons/connectors/default.svg';
      } else {
        if (typeof appIcon === 'string' || appIcon.toString().includes('/assets/icons/connectors/')) {
          isDynamicIcon = true;
        }
        itemIcon = appIcon;
      }
    } else if (sectionType === 'tools' && template.defaultConfig?.appName) {
      itemIcon = getToolIcon(template.type, template.defaultConfig.appName);
    }

    // Generic string-path icon support
    if (!isDynamicIcon && typeof itemIcon === 'string') {
      isDynamicIcon = true;
    }

    // Get appropriate hover color based on section
    let hoverColor = theme.palette.primary.main;
    if (sectionType === 'apps') {
      hoverColor = theme.palette.info.main;
    } else if (sectionType === 'kbs') {
      hoverColor = theme.palette.warning.main;
    }

    return (
      <ListItem
        key={template.type}
        button
        draggable
        onDragStart={(event) => {
          event.dataTransfer.setData('application/reactflow', template.type);
        }}
        sx={{
          py: 0.75,
          px: 2,
          pl: isSubItem ? 5.5 : 4,
          cursor: 'grab',
          borderRadius: 1,
          mx: isSubItem ? 1.5 : 1,
          my: 0.25,
          border: `1px solid ${alpha(theme.palette.divider, isSubItem ? 0.3 : 0.5)}`,
          backgroundColor: isSubItem ? alpha(theme.palette.background.paper, 0.8) : 'transparent',
          '&:hover': {
            backgroundColor: alpha(hoverColor, 0.04),
            borderColor: alpha(hoverColor, 0.2),
          },
          '&:active': {
            cursor: 'grabbing',
          },
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, width: '100%' }}>
          {isDynamicIcon ? (
            <img
              src={itemIcon}
              alt={template.label}
              width={isSubItem ? 16 : 18}
              height={isSubItem ? 16 : 18}
              style={{
                objectFit: 'contain',
              }}
              onError={(e) => {
                e.currentTarget.src = '/assets/icons/connectors/default.svg';
              }}
            />
          ) : (
            <Icon
              icon={itemIcon}
              width={isSubItem ? 16 : 18}
              height={isSubItem ? 16 : 18}
              style={{ color: alpha(theme.palette.text.secondary, 0.7) }}
            />
          )}
          <Typography
            variant="body2"
            sx={{
              fontSize: isSubItem ? '0.85rem' : '0.9rem',
              color: theme.palette.text.primary,
              fontWeight: 400,
              flex: 1,
              lineHeight: 1.4,
            }}
          >
            {normalizeDisplayName(template.label)}
          </Typography>
        </Box>
      </ListItem>
    );
  };

  const renderExpandableGroup = (
    groupLabel: string,
    groupIcon: any,
    itemCount: number,
    isExpanded: boolean,
    onToggle: () => void,
    dragType?: string,
    borderColor = theme.palette.primary.main
  ) => (
    <ListItem
      button
      draggable={!!dragType}
      onDragStart={
        dragType
          ? (event) => {
              event.dataTransfer.setData('application/reactflow', dragType);
            }
          : undefined
      }
      onClick={onToggle}
      sx={{
        py: 1,
        px: 2,
        pl: 4,
        cursor: dragType ? 'grab' : 'pointer',
        borderRadius: 1.5,
        mx: 1,
        mb: 0.5,
        border: `1px solid ${alpha(theme.palette.divider, 0.5)}`,
        '&:hover': {
          backgroundColor: alpha(theme.palette.text.secondary, 0.05),
          borderColor: alpha(theme.palette.text.secondary, 0.2),
        },
        '&:active': {
          cursor: dragType ? 'grabbing' : 'pointer',
        },
      }}
    >
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, width: '100%' }}>
        <Icon
          icon={isExpanded ? chevronDownIcon : chevronRightIcon}
          width={14}
          height={14}
          style={{ color: theme.palette.text.secondary }}
        />
        {typeof groupIcon === 'string' && groupIcon.toString().includes('assets/icons/connectors/') ? (
          <img
            src={groupIcon}
            alt={groupLabel}
            width={18}
            height={18}
            style={{ objectFit: 'contain' }}
            onError={(e) => {
              e.currentTarget.src = '/assets/icons/connectors/default.svg';
            }}
          />
        ) : (
          <Icon
            icon={groupIcon}
            width={18}
            height={18}
            style={{ color: theme.palette.text.secondary }}
          />
        )}
        <Typography
          variant="body2"
          sx={{
            flex: 1,
            fontSize: '0.875rem',
            color: theme.palette.text.primary,
            fontWeight: 500,
          }}
        >
          {groupLabel}
        </Typography>
        <Typography
          variant="caption"
          sx={{
            fontSize: '0.7rem',
            color: alpha(theme.palette.text.secondary, 0.6),
            fontWeight: 500,
            backgroundColor: alpha(theme.palette.text.secondary, 0.1),
            px: 0.75,
            py: 0.25,
            borderRadius: 1,
          }}
        >
          {itemCount}
        </Typography>
      </Box>
    </ListItem>
  );

  const renderDropdownContent = (
    items: NodeTemplate[],
    borderColor = theme.palette.primary.main,
    sectionType?: 'tools' | 'apps' | 'kbs'
  ) => (
    <Box
      sx={{
        position: 'relative',
        '&::before': {
          content: '""',
          position: 'absolute',
          left: '32px',
          top: 0,
          bottom: 0,
          width: '2px',
          backgroundColor: alpha(borderColor, 0.2),
          borderRadius: '1px',
        },
      }}
    >
      <List dense sx={{ py: 0.5 }}>
        {items.map((item) => renderDraggableItem(item, true, sectionType))}
      </List>
    </Box>
  );

  const categoryConfig = [
    {
      name: 'Input / Output',
      icon: inputOutputIcon,
      categories: ['inputs', 'outputs'],
    },
    {
      name: 'Agents',
      icon: agentIcon,
      categories: ['agent'],
    },
    {
      name: 'LLM Models',
      icon: modelIcon,
      categories: ['llm'],
    },
    {
      name: 'Knowledge',
      icon: dataIcon,
      categories: ['knowledge'],
    },
    {
      name: 'Tools',
      icon: processingIcon,
      categories: ['tools'],
    },
    {
      name: 'Vector Stores',
      icon: vectorIcon,
      categories: ['vector'],
    },
  ];

  const getAppIcon = (appName: string) => {
    const iconMap: Record<string, any> = {
      Calculator: calculatorIcon,
      Gmail: googleGmailIcon,
      'Google Calendar': googleCalendarIcon,
      'Google Drive': googleDriveIcon,
      Confluence: confluenceIcon,
      GitHub: githubIcon,
      Jira: jiraIcon,
      Slack: slackIcon,
      'Google Drive Enterprise': googleDriveIcon,
      Calendar: googleCalendarIcon,
      Drive: googleDriveIcon,
      Docs: googleDocsIcon,
      Meet: googleMeetIcon,
      Notion: '/assets/icons/connectors/notion.svg',
      Onedrive: microsoftOnedriveIcon,
      Sharepointonline: '/assets/icons/connectors/sharepoint.svg',
      Outlook: '/assets/icons/connectors/outlook.svg',
      Discord: '/assets/icons/connectors/discord.svg',
      Linear: '/assets/icons/connectors/linear.svg',
      LinkedIn: '/assets/icons/connectors/linkedin.svg',
      Dropbox: '/assets/icons/connectors/dropbox.svg',
      Freshdesk: '/assets/icons/connectors/freshdesk.svg',
      Zendesk: '/assets/icons/connectors/zendesk.svg',
      Posthog: '/assets/icons/connectors/posthog.svg',
      Box: '/assets/icons/connectors/box.svg',
      Bookstack: '/assets/icons/connectors/bookstack.svg',
      Azureblob: '/assets/icons/connectors/azureblob.svg',
      Airtable: '/assets/icons/connectors/airtable.svg',
      Evernote: '/assets/icons/connectors/evernote.svg',
      S3: '/assets/icons/connectors/s3.svg',
      Github: '/assets/icons/connectors/github.svg',
      Gitlab: '/assets/icons/connectors/gitlab.svg',
    };
    return iconMap[appName] || applicationIcon;
  };

  const getAppKnowledgeIcon = (appName: string) => {
    // First try to find the connector in our dynamic data
    const connector = allConnectors.find(
      (c) => c.name.toUpperCase() === appName.toUpperCase() || c.name === appName
    );

    if (connector && connector.iconPath) {
      // Return a placeholder that will be replaced with img tag in renderDraggableItem
      return 'dynamic-icon';
    }

    // Fallback to hardcoded icons for backward compatibility
    const iconMap: Record<string, any> = {
      SLACK: slackIcon,
      GMAIL: googleGmailIcon,
      GOOGLE_DRIVE: googleDriveIcon,
      GOOGLE_WORKSPACE: googleWorkspaceIcon,
      ONEDRIVE: microsoftOnedriveIcon,
      JIRA: jiraIcon,
      CONFLUENCE: confluenceIcon,
      GITHUB: githubIcon,
      Slack: slackIcon,
      Gmail: googleGmailIcon,
      OneDrive: microsoftOnedriveIcon,
      Jira: jiraIcon,
      Confluence: confluenceIcon,
      GitHub: githubIcon,
      Calculator: calculatorIcon,
      'Google Drive': googleDriveIcon,
      'Google Workspace': googleWorkspaceIcon,
      Calendar: googleCalendarIcon,
      Drive: googleDriveIcon,
      Docs: googleDocsIcon,
      Meet: googleMeetIcon,
      Notion: notionIcon,
      Onedrive: microsoftOnedriveIcon,
      Sharepoint: 'assets/icons/connectors/sharepoint.svg',
      Outlook: 'assets/icons/connectors/outlook.svg',
    };

    return iconMap[appName] || cloudIcon;
  };

  const getToolIcon = (toolType: string, appName: string) => {
    // Normalize toolType to handle both dot notation and underscore notation
    const normalizedType = toolType.toLowerCase();

    // Gmail specific icons
    if (appName === 'Gmail') {
      if (normalizedType.includes('reply')) return replyIcon;
      if (normalizedType.includes('send')) return emailSendIcon;
      if (normalizedType.includes('draft')) return emailEditIcon;
      if (normalizedType.includes('search')) return emailSearchIcon;
      if (normalizedType.includes('details') || normalizedType.includes('get'))
        return emailOpenIcon;
      if (normalizedType.includes('attachment')) return paperclipIcon;
      if (normalizedType.includes('compose')) return emailEditIcon;
      return emailIcon;
    }

    // Google Calendar specific icons
    if (appName === 'Google Calendar') {
      if (normalizedType.includes('create') || normalizedType.includes('add'))
        return calendarPlusIcon;
      if (normalizedType.includes('update') || normalizedType.includes('edit'))
        return calendarEditIcon;
      if (normalizedType.includes('delete') || normalizedType.includes('remove'))
        return calendarRemoveIcon;
      if (normalizedType.includes('get') || normalizedType.includes('list'))
        return calendarSearchIcon;
      if (normalizedType.includes('event')) return calendarClockIcon;
      return calendarIcon;
    }

    // Jira specific icons
    if (appName === 'Jira') {
      if (normalizedType.includes('create')) return plusCircleOutlineIcon;
      if (normalizedType.includes('update') || normalizedType.includes('edit'))
        return pencilOutlineIcon;
      if (normalizedType.includes('delete')) return deleteOutlineIcon;
      if (normalizedType.includes('search')) return searchIcon;
      if (normalizedType.includes('comment')) return commentOutlineIcon;
      if (normalizedType.includes('assign')) return accountPlusOutlineIcon;
      if (normalizedType.includes('transition')) return arrowRightCircleOutlineIcon;
      if (normalizedType.includes('issue')) return bugOutlineIcon;
      return jiraIcon;
    }

    // Slack specific icons
    if (appName === 'Slack') {
      if (normalizedType.includes('send') || normalizedType.includes('message'))
        return sendOutlineIcon;
      if (normalizedType.includes('channel')) return poundBoxOutlineIcon;
      if (normalizedType.includes('search')) return searchIcon;
      if (normalizedType.includes('user') || normalizedType.includes('info'))
        return accountOutlineIcon;
      if (normalizedType.includes('create')) return plusCircleOutlineIcon;
      return slackIcon;
    }

    // GitHub specific icons
    if (appName === 'GitHub') {
      if (normalizedType.includes('repo') || normalizedType.includes('repository'))
        return sourceRepositoryIcon;
      if (normalizedType.includes('issue')) return bugOutlineIcon;
      if (normalizedType.includes('pull') || normalizedType.includes('pr')) return sourcePullIcon;
      if (normalizedType.includes('commit')) return sourceCommitIcon;
      if (normalizedType.includes('branch')) return sourceBranchIcon;
      if (normalizedType.includes('create')) return plusCircleOutlineIcon;
      if (normalizedType.includes('search')) return searchIcon;
      return githubIcon;
    }

    // Google Drive specific icons
    if (appName.includes('Google Drive')) {
      if (normalizedType.includes('upload')) return uploadIcon;
      if (normalizedType.includes('download')) return downloadIcon;
      if (normalizedType.includes('create') && normalizedType.includes('folder'))
        return folderPlusOutlineIcon;
      if (normalizedType.includes('delete')) return deleteOutlineIcon;
      if (normalizedType.includes('list') || normalizedType.includes('get'))
        return fileDocumentMultipleOutlineIcon;
      if (normalizedType.includes('share')) return shareVariantOutlineIcon;
      if (normalizedType.includes('folder')) return folderOutlineIcon;
      return googleDriveIcon;
    }

    // Confluence specific icons
    if (appName === 'Confluence') {
      if (normalizedType.includes('create') || normalizedType.includes('add'))
        return plusCircleOutlineIcon;
      if (normalizedType.includes('update') || normalizedType.includes('edit'))
        return pencilOutlineIcon;
      if (normalizedType.includes('delete')) return deleteOutlineIcon;
      if (normalizedType.includes('search')) return searchIcon;
      if (normalizedType.includes('page')) return fileDocumentOutlineIcon;
      if (normalizedType.includes('space')) return folderMultipleOutlineIcon;
      return confluenceIcon;
    }

    // Calculator specific icons
    if (appName === 'Calculator') {
      if (normalizedType.includes('add') || normalizedType.includes('plus'))
        return plusCircleOutlineIcon;
      if (normalizedType.includes('subtract') || normalizedType.includes('minus'))
        return minusCircleOutlineIcon;
      if (normalizedType.includes('multiply') || normalizedType.includes('times'))
        return closeCircleOutlineIcon;
      if (normalizedType.includes('divide')) return divisionIcon;
      if (normalizedType.includes('calculate')) return equalIcon;
      return calculatorIcon;
    }

    // Default icons based on common actions
    if (normalizedType.includes('send')) return sendIcon;
    if (normalizedType.includes('create') || normalizedType.includes('add'))
      return plusCircleOutlineIcon;
    if (normalizedType.includes('update') || normalizedType.includes('edit'))
      return pencilOutlineIcon;
    if (normalizedType.includes('delete') || normalizedType.includes('remove'))
      return deleteOutlineIcon;
    if (normalizedType.includes('search') || normalizedType.includes('find')) return searchIcon;
    if (normalizedType.includes('get') || normalizedType.includes('fetch'))
      return downloadOutlineIcon;
    if (normalizedType.includes('list') || normalizedType.includes('all'))
      return formatListBulletIcon;

    // Default fallback
    return cogOutlineIcon;
  };

  return (
    <Drawer
      variant="persistent"
      anchor="left"
      open={sidebarOpen}
      sx={{
        width: sidebarOpen ? sidebarWidth : 0,
        flexShrink: 0,
        transition: theme.transitions.create(['width'], {
          easing: theme.transitions.easing.sharp,
          duration: theme.transitions.duration.leavingScreen,
        }),
        '& .MuiDrawer-paper': {
          width: sidebarWidth,
          boxSizing: 'border-box',
          border: 'none',
          borderRight: `1px solid ${theme.palette.divider}`,
          backgroundColor: theme.palette.background.paper,
          zIndex: theme.zIndex.drawer - 1,
          position: 'relative',
          height: '100%',
          overflowX: 'hidden',
          boxShadow: 'none',
          transition: theme.transitions.create(['width'], {
            easing: theme.transitions.easing.sharp,
            duration: theme.transitions.duration.leavingScreen,
          }),
        },
      }}
    >
      {/* Header */}
      <Box
        sx={{
          p: 2,
          borderBottom: `1px solid ${theme.palette.divider}`,
          backgroundColor: theme.palette.background.paper,
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 2 }}>
          <Box
            sx={{
              width: 24,
              height: 24,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <Icon
              icon={bundleIcon}
              width={20}
              height={20}
              style={{ color: theme.palette.text.primary }}
            />
          </Box>
          <Typography
            variant="h6"
            sx={{
              fontWeight: 600,
              color: theme.palette.text.primary,
              fontSize: '1rem',
              flex: 1,
            }}
          >
            Components
          </Typography>
        </Box>

        <TextField
          fullWidth
          size="small"
          placeholder="Search"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Icon
                  icon={searchIcon}
                  fontSize={16}
                  style={{ color: theme.palette.text.secondary }}
                />
              </InputAdornment>
            ),
            endAdornment: searchQuery && (
              <InputAdornment position="end">
                <IconButton
                  size="small"
                  onClick={() => setSearchQuery('')}
                  sx={{
                    p: 0.25,
                    color: theme.palette.text.secondary,
                  }}
                >
                  <Icon icon={clearIcon} fontSize={14} />
                </IconButton>
              </InputAdornment>
            ),
          }}
          sx={{
            '& .MuiOutlinedInput-root': {
              borderRadius: 1.5,
              backgroundColor: alpha(theme.palette.background.default, 0.5),
              border: `1px solid ${theme.palette.divider}`,
              height: 36,
              '&:hover': {
                backgroundColor: alpha(theme.palette.background.default, 0.8),
                borderColor: alpha(theme.palette.text.secondary, 0.3),
              },
              '&.Mui-focused': {
                backgroundColor: theme.palette.background.default,
                borderColor: theme.palette.text.secondary,
              },
              '& fieldset': {
                border: 'none',
              },
            },
            '& .MuiInputBase-input': {
              color: theme.palette.text.primary,
              fontSize: '0.875rem',
              padding: '8px 0',
              '&::placeholder': {
                color: theme.palette.text.secondary,
                opacity: 0.7,
              },
            },
          }}
        />
        {searchQuery && (
          <Typography
            variant="caption"
            sx={{
              mt: 1,
              display: 'block',
              fontSize: '0.7rem',
              color: theme.palette.text.secondary,
              opacity: 0.8,
            }}
          >
            /{searchQuery}
          </Typography>
        )}
      </Box>

      {/* Sidebar Content */}
      <Box
        sx={{
          overflow: 'auto',
          height: 'calc(100% - 140px)',
          minHeight: 0,
          overflowX: 'hidden',
          '&::-webkit-scrollbar': {
            width: '4px',
          },
          '&::-webkit-scrollbar-track': {
            background: 'transparent',
          },
          '&::-webkit-scrollbar-thumb': {
            backgroundColor: alpha(theme.palette.text.secondary, 0.2),
            borderRadius: '8px',
            '&:hover': {
              backgroundColor: alpha(theme.palette.text.secondary, 0.3),
            },
          },
        }}
      >
        {loading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', p: 3 }}>
            <CircularProgress size={24} />
          </Box>
        ) : (
          <Box>
            {/* Main Categories */}
            {categoryConfig.map((config) => {
              let categoryTemplates = filteredTemplates.filter((t) =>
                config.categories.includes(t.category)
              );

              // For Tools category, only show app-level tool groups
              if (config.name === 'Tools') {
                categoryTemplates = appToolNodes;
              }

              const isExpanded = expandedCategories[config.name];
              const hasItems = categoryTemplates.length > 0;

              return (
                <Box key={config.name}>
                  <ListItem
                    button
                    onClick={() => handleCategoryToggle(config.name)}
                    sx={{
                      py: 1,
                      px: 2,
                      cursor: 'pointer',
                      '&:hover': {
                        backgroundColor: alpha(theme.palette.text.secondary, 0.05),
                      },
                    }}
                  >
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, width: '100%' }}>
                      <Icon
                        icon={isExpanded ? chevronDownIcon : chevronRightIcon}
                        width={16}
                        height={16}
                        style={{ color: theme.palette.text.secondary }}
                      />
                      <Icon
                        icon={config.icon}
                        width={16}
                        height={16}
                        style={{ color: theme.palette.text.secondary }}
                      />
                      <Typography
                        variant="body2"
                        sx={{
                          flex: 1,
                          fontSize: '0.875rem',
                          color: theme.palette.text.primary,
                          fontWeight: 500,
                        }}
                      >
                        {config.name}
                      </Typography>
                    </Box>
                  </ListItem>

                  <Collapse in={isExpanded} timeout="auto" unmountOnExit>
                    {config.name === 'Tools' ? (
                      <Box sx={{ pl: 0 }}>
                        {Object.entries(groupedByApp).map(([appName, tools]) => {
                          const isAppExpanded = expandedApps[appName];
                          const appGroup = appToolNodes.find(
                            (node) => node.defaultConfig?.appDisplayName === appName
                          );

                          return (
                            <Box key={appName}>
                              {renderExpandableGroup(
                                appName,
                                getAppIcon(appName),
                                tools.length,
                                isAppExpanded,
                                () => handleAppToggle(appName),
                                appGroup?.type
                              )}
                              <Collapse in={isAppExpanded} timeout="auto" unmountOnExit>
                                {renderDropdownContent(tools, theme.palette.primary.main, 'tools')}
                              </Collapse>
                            </Box>
                          );
                        })}
                      </Box>
                    ) : config.name === 'LLM Models' ? (
                      <List dense sx={{ py: 0 }}>
                        {categoryTemplates.map((template) => renderDraggableItem(template))}
                      </List>
                    ) : config.name === 'Knowledge' ? (
                      <Box sx={{ pl: 0 }}>
                        {/* App Memory group with dropdown */}
                        {appKnowledgeGroupNode && (
                          <>
                            {renderExpandableGroup(
                              appKnowledgeGroupNode.label,
                              appKnowledgeGroupNode.icon,
                              individualAppKnowledge.length,
                              expandedApps.app,
                              () => handleAppToggle('app'),
                              appKnowledgeGroupNode.type,
                              theme.palette.info.main
                            )}
                            <Collapse in={expandedApps.app} timeout="auto" unmountOnExit>
                              {renderDropdownContent(
                                individualAppKnowledge,
                                theme.palette.info.main,
                                'apps'
                              )}
                            </Collapse>
                          </>
                        )}

                        {/* Knowledge Bases group with dropdown */}
                        {kbGroupNode && (
                          <>
                            {renderExpandableGroup(
                              kbGroupNode.label,
                              kbGroupNode.icon,
                              individualKBs.length,
                              expandedApps['knowledge-bases'],
                              () => handleAppToggle('knowledge-bases'),
                              kbGroupNode.type,
                              theme.palette.warning.main
                            )}
                            <Collapse
                              in={expandedApps['knowledge-bases']}
                              timeout="auto"
                              unmountOnExit
                            >
                              {renderDropdownContent(
                                individualKBs,
                                theme.palette.warning.main,
                                'kbs'
                              )}
                            </Collapse>
                          </>
                        )}

                        {/* Other memory components that aren't apps or KBs */}
                        {categoryTemplates
                          .filter((t) => !t.type.startsWith('kb-') && !t.type.startsWith('app-'))
                          .map((template) => renderDraggableItem(template))}
                      </Box>
                    ) : hasItems ? (
                      <List dense sx={{ py: 0 }}>
                        {categoryTemplates.map((template) => renderDraggableItem(template))}
                      </List>
                    ) : (
                      <Box sx={{ pl: 4, py: 1 }}>
                        <Typography
                          variant="caption"
                          sx={{
                            color: alpha(theme.palette.text.secondary, 0.6),
                            fontSize: '0.75rem',
                            fontStyle: 'italic',
                          }}
                        >
                          No components available
                        </Typography>
                      </Box>
                    )}
                  </Collapse>
                </Box>
              );
            })}
          </Box>
        )}
      </Box>
    </Drawer>
  );
};

export default FlowBuilderSidebar;
