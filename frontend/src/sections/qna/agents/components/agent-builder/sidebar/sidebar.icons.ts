// Sidebar icon mappings
// Centralized icon imports and mappings for the flow builder sidebar

import { Icon } from '@iconify/react';

// Basic UI icons
import chevronDownIcon from '@iconify-icons/mdi/chevron-down';
import chevronRightIcon from '@iconify-icons/mdi/chevron-right';
import searchIcon from '@iconify-icons/mdi/magnify';
import clearIcon from '@iconify-icons/mdi/close';
import inputOutputIcon from '@iconify-icons/mdi/swap-horizontal';
import settingsIcon from '@iconify-icons/mdi/open-in-new';
import alertCircleIcon from '@iconify-icons/mdi/alert-circle-outline';
import connectorIcon from '@iconify-icons/mdi/database-plus';

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
import calculatorIcon from '@iconify-icons/mdi/calculator';
import googleDocsIcon from '@iconify-icons/logos/google';
import googleMeetIcon from '@iconify-icons/logos/google-meet';
import notionIcon from '@iconify-icons/logos/notion';
import authenticatedIcon from '@iconify-icons/mdi/shield-check-outline';

/**
 * Export all UI icons
 */
export const UI_ICONS = {
  chevronDown: chevronDownIcon,
  chevronRight: chevronRightIcon,
  search: searchIcon,
  clear: clearIcon,
  inputOutput: inputOutputIcon,
  settings: settingsIcon,
  alertCircle: alertCircleIcon,
  connector: connectorIcon,
  authenticated: authenticatedIcon,
} as const;

/**
 * Export all category icons
 */
export const CATEGORY_ICONS = {
  agent: agentIcon,
  model: modelIcon,
  data: dataIcon,
  vector: vectorIcon,
  processing: processingIcon,
  bundle: bundleIcon,
  cloud: cloudIcon,
  application: applicationIcon,
  inputOutput: inputOutputIcon,
} as const;

/**
 * Get app/connector knowledge icon based on app name
 */
export const getAppKnowledgeIcon = (appName: string, connectors: any[]): any => {
  // First try to find the connector in our dynamic data
  const connector = connectors.find(
    (c) => c.name.toUpperCase() === appName.toUpperCase() || c.name === appName
  );

  if (connector?.iconPath) {
    return 'dynamic-icon'; // Signal to use img tag
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
    Sharepoint: '/assets/icons/connectors/sharepoint.svg',
    Outlook: '/assets/icons/connectors/outlook.svg',
  };

  return iconMap[appName] || cloudIcon;
};

/**
 * Get tool icon based on tool type and app name
 */
export const getToolIcon = (toolType: string, appName: string): any => {
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
  return settingsIcon;
};

