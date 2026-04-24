import type { CSSProperties } from 'react';
import type { MoreConnectorLink } from './types';

export const DIALOG_STYLES = {
  overlayBg: 'rgba(28, 32, 36, 0.3)' as CSSProperties['backgroundColor'],
  contentBg: 'var(--color-panel-solid)' as CSSProperties['backgroundColor'],
  boxShadow:
    '0 16px 36px -20px rgba(0, 6, 46, 0.2), 0 16px 64px rgba(0, 0, 85, 0.02), 0 12px 60px rgba(0, 0, 0, 0.15)' as CSSProperties['boxShadow'],
} as const;

/**
 * Page size for knowledge hub sidebar: root app list, per-app children, and
 * `load more` fetches. Set to 5 for pagination testing; increase (e.g. 50) for production.
 */
export const SIDEBAR_PAGINATION_PAGE_SIZE = 5;

// Curated "More Connectors" links for the All Records sidebar.
// Top 5 enterprise connectors per scope, based on industry adoption.

export const ADMIN_MORE_CONNECTORS: MoreConnectorLink[] = [
  { id: 'jira-link', name: 'Jira', type: 'jira', connectorTypeParam: 'Jira' },
  { id: 'confluence-link', name: 'Confluence', type: 'confluence', connectorTypeParam: 'Confluence' },
  { id: 'sharepoint-link', name: 'SharePoint', type: 'sharepoint', connectorTypeParam: 'SharePoint Online' },
  { id: 'drive-workspace-link', name: 'Google Drive', type: 'google-drive', connectorTypeParam: 'Drive Workspace' },
  { id: 'notion-link', name: 'Notion', type: 'notion', connectorTypeParam: 'Notion' },
];

export const PERSONAL_MORE_CONNECTORS: MoreConnectorLink[] = [
  { id: 'drive-link', name: 'Drive', type: 'google-drive', connectorTypeParam: 'Drive' },
  { id: 'gmail-link', name: 'Gmail', type: 'gmail', connectorTypeParam: 'Gmail' },
  { id: 'outlook-personal-link', name: 'Outlook Personal', type: 'outlook', connectorTypeParam: 'Outlook Personal' },
  { id: 'dropbox-personal-link', name: 'Dropbox Personal', type: 'dropbox', connectorTypeParam: 'Dropbox Personal' },
  { id: 'nextcloud-link', name: 'Nextcloud', type: 'nextcloud', connectorTypeParam: 'Nextcloud' },
];

export const FOLDER_REINDEX_DEPTH = 100;
