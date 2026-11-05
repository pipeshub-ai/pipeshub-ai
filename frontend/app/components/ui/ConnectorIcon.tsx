'use client'

import React, { useState } from "react";
import Image from "next/image";
import { MaterialIcon } from "./MaterialIcon";
import { useThemeAppearance } from '@/app/components/theme-provider';

/**
 * ConnectorIcon component renders custom SVG icons for connectors with MaterialIcon fallback
 */

export type ConnectorType =
  | "intercom"
  | 'slack'
  | 'teams'
  | 'zoom'
  | 'gmail'
  | 'outlook'
  | 'google-meet'
  | 'google-calendar'
  | 'google-drive'
  | 'onedrive'
  | 'dropbox'
  | 'box'
  | 'amazon-s3'
  | 'gcs'
  | 'minio'
  | 'azure-blob'
  | 'azure-files'
  | 'azure-fileshares'
  | 'local-fs'
  | 'localfs'
  | 'nextcloud'
  | 'smb'
  | 'cifs'
  | 'notion'
  | 'confluence'
  | 'bookstack'
  | 'drupal-wiki'
  | 'kb'
  | 'knowledge-base'
  | 'google-docs'
  | 'google-sheets'
  | 'google-slides'
  | 'google-forms'
  | 'ms-onenote'
  | 'microsoft-learn'
  | 'jira'
  | 'linear'
  | 'gitlab'
  | 'github'
  | 'servicenow'
  | 'zendesk'
  | 'zammad'
  | 'sharepoint'
  | 'sharepoint-online'
  | 'google-admin'
  | 'google-cloud'
  | 'salesforce'
  | 'postgresql'
  | 'mariadb'
  | 'snowflake'
  | 'airtable'
  | 'youtube'
  | 'rss'
  | 'seek'
  | 'frame'
  | 'vector'
  | 'clickup'
  | 'redshift'
  | 'lumos'
  | 'exa'
  | 'context7'
  | 'huggingface'
  | 'atlassian'
  | 'discord'
  | 'posthog'
  | 'brave'
  | 'stripe'
  | 'square'
  | 'paypal'
  | 'web'
  | 'api'
  | 'generic';

interface ConnectorIconProps {
  type: ConnectorType | string;
  size?: number;
  color?: string;
  style?: React.CSSProperties;
}

const CONNECTOR_ICONS_BASE_PATH = '/icons/connectors';
const svg = (name: string) => `${CONNECTOR_ICONS_BASE_PATH}/${name}.svg`;

export const CONNECTOR_ICONS: Record<ConnectorType, { svg: string | null; fallback: string; needDarkModeInvert?: boolean }> = {
  'intercom': { svg: svg('intercom'), fallback: 'hub' },
  'slack': { svg: svg('slack'), fallback: 'tag' },
  'teams': { svg: svg('teams'), fallback: 'groups' },
  'zoom': { svg: svg('zoom'), fallback: 'videocam' },
  'gmail': { svg: svg('gmail'), fallback: 'mail' },
  'outlook': { svg: svg('outlook'), fallback: 'mail' },
  'google-meet': { svg: svg('meet'), fallback: 'videocam' },
  'google-calendar': { svg: svg('calendar'), fallback: 'calendar_today' },
  'google-drive': { svg: svg('drive'), fallback: 'cloud' },
  'onedrive': { svg: svg('onedrive'), fallback: 'cloud_upload' },
  'dropbox': { svg: svg('dropbox'), fallback: 'cloud_upload' },
  'box': { svg: svg('box'), fallback: 'inventory_2' },
  'amazon-s3': { svg: svg('s3'), fallback: 'cloud' },
  'gcs': { svg: svg('gcs'), fallback: 'cloud' },
  'minio': { svg: svg('minio'), fallback: 'cloud' },
  'azure-blob': { svg: svg('azureblob'), fallback: 'cloud' },
  'azure-files': { svg: svg('azurefiles'), fallback: 'folder_shared' },
  'azure-fileshares': { svg: svg('azurefiles'), fallback: 'folder_shared' },
  'local-fs': { svg: `${CONNECTOR_ICONS_BASE_PATH}/local-fs.png`, fallback: 'folder', needDarkModeInvert: true },
  'localfs': { svg: `${CONNECTOR_ICONS_BASE_PATH}/local-fs.png`, fallback: 'folder', needDarkModeInvert: true },
  'nextcloud': { svg: svg('nextcloud'), fallback: 'cloud' },
  'smb': { svg: svg('smb'), fallback: 'folder_shared' },
  'cifs': { svg: svg('cifs'), fallback: 'folder_shared' },
  'notion': { svg: svg('notion'), fallback: 'description' },
  'confluence': { svg: svg('confluence'), fallback: 'article' },
  'bookstack': { svg: svg('bookstack'), fallback: 'menu_book' },
  'drupal-wiki': { svg: svg('drupalwiki'), fallback: 'menu_book' },
  'google-docs': { svg: svg('docs'), fallback: 'description' },
  'google-sheets': { svg: svg('sheets'), fallback: 'table_chart' },
  'google-slides': { svg: svg('slides'), fallback: 'slideshow' },
  'google-forms': { svg: svg('forms'), fallback: 'quiz' },
  'ms-onenote': { svg: svg('ms-onenote'), fallback: 'note' },
  'microsoft-learn': { svg: svg('microsoft-learn'), fallback: 'menu_book' },
  'kb': { svg: svg('kb'), fallback: 'folder' },
  'knowledge-base': { svg: svg('kb'), fallback: 'folder' },
  'github': { svg: svg('github'), fallback: 'code', needDarkModeInvert: true },
  'gitlab': { svg: svg('gitlab'), fallback: 'code' },
  'jira': { svg: svg('jira'), fallback: 'bug_report' },
  'linear': { svg: svg('linear'), fallback: 'view_kanban', needDarkModeInvert: true },
  'servicenow': { svg: svg('servicenow'), fallback: 'build' },
  'zendesk': { svg: svg('zendesk'), fallback: 'support' },
  'zammad': { svg: svg('zammad'), fallback: 'support_agent' },
  'sharepoint': { svg: svg('sharepoint'), fallback: 'share' },
  'sharepoint-online': { svg: svg('sharepoint'), fallback: 'share' },
  'google-admin': { svg: svg('google-admin'), fallback: 'admin_panel_settings' },
  'google-cloud': { svg: svg('gcs'), fallback: 'cloud' },
  'salesforce': { svg: svg('salesforce'), fallback: 'cloud' },
  'postgresql': { svg: svg('postgresql'), fallback: 'storage' },
  'mariadb': { svg: svg('mariadb'), fallback: 'storage' },
  'snowflake': { svg: svg('snowflake'), fallback: 'ac_unit' },
  'airtable': { svg: svg('airtable'), fallback: 'grid_view' },
  'youtube': { svg: svg('yt'), fallback: 'smart_display' },
  'rss': { svg: svg('rss'), fallback: 'rss_feed' },
  'seek': { svg: svg('seek'), fallback: 'search' },
  'frame': { svg: svg('frame'), fallback: 'frame_inspect' },
  'vector': { svg: svg('vector'), fallback: 'data_array' },
  'clickup': { svg: svg('clickup'), fallback: 'task_alt' },
  'redshift': { svg: svg('redshift'), fallback: 'storage' },
  'lumos': { svg: svg('lumos'), fallback: 'manage_accounts' },
  'exa': { svg: svg('exa'), fallback: 'travel_explore' },
  'context7': { svg: svg('context7'), fallback: 'menu_book' },
  'huggingface': { svg: svg('huggingface'), fallback: 'smart_toy' },
  'atlassian': { svg: svg('atlassian'), fallback: 'account_tree' },
  'discord': { svg: null, fallback: 'forum' },
  'posthog': { svg: null, fallback: 'insights' },
  'brave': { svg: svg('brave'), fallback: 'travel_explore' },
  'stripe': { svg: svg('stripe'), fallback: 'payments' },
  'square': { svg: svg('square'), fallback: 'store' },
  'paypal': { svg: svg('paypal'), fallback: 'payments' },
  'web': { svg: svg('web'), fallback: 'language' },
  'api': { svg: null, fallback: 'api' },
  'generic': { svg: svg('default'), fallback: 'extension' },
};

const FUZZY_MATCH_RULES: Array<[string, ConnectorType]> = [
  ['api-action:', 'api'],
  ['google-cloud-storage', 'gcs'],
  ['google-drive', 'google-drive'], ['gdrive', 'google-drive'],
  ['google-docs', 'google-docs'], ['google-forms', 'google-forms'],
  ['google-meet', 'google-meet'], ['google-sheets', 'google-sheets'],
  ['google-slides', 'google-slides'], ['google-admin', 'google-admin'],
  ['google-cloud', 'google-cloud'], ['google-calendar', 'google-calendar'],
  ['gmail', 'gmail'],
  ['sharepointonline', 'sharepoint-online'], ['sharepoint-online', 'sharepoint-online'],
  ['sharepoint', 'sharepoint'],
  ['onedrive', 'onedrive'], ['outlook', 'outlook'],
  ['onenote', 'ms-onenote'], ['ms-note', 'ms-onenote'],
  ['github-teams', 'github'], ['githubteams', 'github'], ['github', 'github'],
  ['teams', 'teams'],
  ['zoom', 'zoom'],
  ['azure-files', 'azure-files'],
  ['amazon-s3', 'amazon-s3'], ['aws-s3', 'amazon-s3'], ['s3', 'amazon-s3'],
  ['azure-fileshare', 'azure-fileshares'], ['azure-blob', 'azure-blob'],
  ['azure-storage', 'azure-blob'],
  ['local-filesystem', 'local-fs'], ['local-fs', 'local-fs'], ['localfs', 'local-fs'],
  ['local-files', 'local-fs'],
  ['dropbox', 'dropbox'], ['box', 'box'],
  ['minio', 'minio'], ['nextcloud', 'nextcloud'],
  ['network-drive', 'smb'], ['smb3', 'smb'], ['smb2', 'smb'], ['smb', 'smb'],
  ['cifs', 'cifs'],
  ['linear', 'linear'],
  ['jira', 'jira'], ['confluence', 'confluence'],
  ['gitlab', 'gitlab'], ['slack', 'slack'],
  ['servicenow', 'servicenow'], ['service-now', 'servicenow'],
  ['zendesk', 'zendesk'], ['zammad', 'zammad'],
  ['snowflake', 'snowflake'], ['postgresql', 'postgresql'], ['postgres', 'postgresql'],
  ['mariadb', 'mariadb'], ['airtable', 'airtable'],
  ['notion', 'notion'], ['bookstack', 'bookstack'],
  ['drupal', 'drupal-wiki'],
  ['youtube', 'youtube'], ['rss', 'rss'],
  ['seek', 'seek'], ['frame', 'frame'], ['vector', 'vector'],
  ['atlassian', 'atlassian'], ['rovo', 'atlassian'],
  ['exa', 'exa'], ['discord', 'discord'], ['posthog', 'posthog'],
  ['hugging-face', 'huggingface'], ['huggingface', 'huggingface'],
  ['brave', 'brave'],
  ['stripe', 'stripe'],
  ['paypal', 'paypal'],
  ['docs', 'google-docs'], ['sheets', 'google-sheets'],
  ['google', 'google-drive'], ['microsoft', 'sharepoint'], ['365', 'sharepoint'],
  ['azure', 'azure-blob'], ['drive', 'google-drive'],
  ['calendar', 'google-calendar'],
  ['web', 'web'],
  ['clickup', 'clickup'], ['click-up', 'clickup'],
  ['redshift', 'redshift'], ['red-shift', 'redshift'],
  ['lumos', 'lumos'],
  ['kb', 'kb'],
  ['knowledge-base', 'knowledge-base'],
];

export function resolveConnectorType(input: string): ConnectorType {
  if (!input) return 'generic';
  if (input in CONNECTOR_ICONS) return input as ConnectorType;

  const normalized = input.toLowerCase().replace(/[_\s]+/g, '-');
  if (normalized in CONNECTOR_ICONS) return normalized as ConnectorType;

  const tokens = normalized.split('-');
  if (tokens.includes('square') || normalized === 'squareup') {
    return 'square';
  }

  for (const [pattern, type] of FUZZY_MATCH_RULES) {
    if (normalized.includes(pattern)) return type;
  }

  return 'generic';
}

export function getConnectorIconConfig(input: string): { svg: string | null; fallback: string; needDarkModeInvert?: boolean; } {
  const type = resolveConnectorType(input);
  return CONNECTOR_ICONS[type];
}

export const ConnectorIcon = ({
  type,
  size = 16,
  color,
  style
}: ConnectorIconProps) => {
  const [imageError, setImageError] = useState(false);
  const { appearance } = useThemeAppearance();
  const isDarkMode = appearance === 'dark';

  const iconConfig = CONNECTOR_ICONS[type as ConnectorType] ?? getConnectorIconConfig(type);

  if (iconConfig.svg && !imageError) {
    const monochromeFilter = iconConfig.needDarkModeInvert
      ? (isDarkMode ? 'brightness(0) invert(1)' : 'brightness(0)')
      : undefined;

    return (
      <Image
        src={iconConfig.svg}
        alt={`${type} icon`}
        width={size}
        height={size}
        unoptimized
        onError={() => setImageError(true)}
        style={{
          objectFit: 'contain',
          display: 'inline-flex',
          filter: monochromeFilter,
          ...style
        }}
      />
    );
  }

  return (
    <MaterialIcon
      name={iconConfig.fallback}
      size={size}
      color={color}
      style={style}
    />
  );
};
