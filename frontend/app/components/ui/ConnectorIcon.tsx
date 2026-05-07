'use client'

import React, { useState } from "react";
import Image from "next/image";
import { MaterialIcon } from "./MaterialIcon";
import { useThemeAppearance } from '@/app/components/theme-provider';

/**
 * ConnectorIcon component renders custom SVG icons for connectors with MaterialIcon fallback
 *
 * Features:
 * - Uses custom brand SVG icons from /public/icons/connectors/ when available
 * - Falls back to Material Icons when SVG doesn't exist
 * - Forces all SVGs to requested size for consistency (ignores native dimensions)
 * - Always shows brand colors for SVG icons (color prop ignored for SVGs)
 * - Type-safe with ConnectorType union
 *
 * @example
 * <ConnectorIcon type="slack" size={16} />
 * <ConnectorIcon type="jira" size={16} color="var(--slate-11)" />
 */

export type ConnectorType =
  // Communication & Collaboration
  | 'slack'
  | 'teams'
  | 'zoom'
  | 'gmail'
  | 'outlook'
  | 'google-meet'
  | 'google-calendar'
  // Cloud Storage & Drives
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
  // Document & Knowledge
  | 'notion'
  | 'confluence'
  | 'bookstack'
  /** PipesHub Collections / KB — backend `Connectors.KNOWLEDGE_BASE` = `"KB"` */
  | 'kb'
  | 'knowledge-base'
  | 'google-docs'
  | 'google-sheets'
  | 'google-slides'
  | 'google-forms'
  | 'ms-onenote'
  // Project & Issue Tracking
  | 'jira'
  | 'linear'
  | 'gitlab'
  | 'github'
  | 'servicenow'
  | 'zendesk'
  | 'zammad'
  // Enterprise & Admin
  | 'sharepoint'
  | 'sharepoint-online'
  | 'google-admin'
  | 'google-cloud'
  | 'salesforce'
  // Databases
  | 'postgresql'
  | 'mariadb'
  | 'snowflake'
  | 'airtable'
  // Media & Other
  | 'youtube'
  | 'rss'
  | 'seek'
  | 'frame'
  | 'vector'
  | 'clickup'
  | 'redshift'
  // Generic / Fallback
  | 'web'
  | 'generic';

interface ConnectorIconProps {
  /** Connector type (determines which icon to show) */
  type: ConnectorType | string;
  /** Icon size in pixels (default: 16) */
  size?: number;
  /** Color for Material Icon fallback only (ignored for SVG icons) */
  color?: string;
  /** Additional inline styles */
  style?: React.CSSProperties;
}

/**
 * Icon mapping configuration
 * - svg: Path to custom SVG icon (null if doesn't exist yet)
 * - fallback: Material Icon name to use when SVG is unavailable
 */
const CONNECTOR_ICONS_BASE_PATH = '/icons/connectors';
// Helper to build full path
const svg = (name: string) => `${CONNECTOR_ICONS_BASE_PATH}/${name}.svg`;

export const CONNECTOR_ICONS: Record<ConnectorType, { svg: string | null; fallback: string; needDarkModeInvert?: boolean }> = {
  // Communication & Collaboration
  'slack': { svg: svg('slack'), fallback: 'tag' },
  'teams': { svg: svg('teams'), fallback: 'groups' },
  'zoom': { svg: svg('zoom'), fallback: 'videocam' },
  'gmail': { svg: svg('gmail'), fallback: 'mail' },
  'outlook': { svg: svg('outlook'), fallback: 'mail' },
  'google-meet': { svg: svg('meet'), fallback: 'videocam' },
  'google-calendar': { svg: svg('calendar'), fallback: 'calendar_today' },
  // Cloud Storage & Drives
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
  // Document & Knowledge
  'notion': { svg: svg('notion'), fallback: 'description', needDarkModeInvert: true },
  'confluence': { svg: svg('confluence'), fallback: 'article' },
  'bookstack': { svg: svg('bookstack'), fallback: 'menu_book' },
  'google-docs': { svg: svg('docs'), fallback: 'description' },
  'google-sheets': { svg: svg('sheets'), fallback: 'table_chart' },
  'google-slides': { svg: svg('slides'), fallback: 'slideshow' },
  'google-forms': { svg: svg('forms'), fallback: 'quiz' },
  'ms-onenote': { svg: svg('ms-onenote'), fallback: 'note' },
  'kb': { svg: svg('kb'), fallback: 'folder' },
  'knowledge-base': { svg: svg('kb'), fallback: 'folder' },
  // Project & Issue Tracking (keys sorted A–Z)
  'github': { svg: svg('github'), fallback: 'code', needDarkModeInvert: true },
  'gitlab': { svg: svg('gitlab'), fallback: 'code' },
  'jira': { svg: svg('jira'), fallback: 'bug_report' },
  'linear': { svg: svg('linear'), fallback: 'view_kanban', needDarkModeInvert: true },
  'servicenow': { svg: svg('servicenow'), fallback: 'build' },
  'zendesk': { svg: svg('zendesk'), fallback: 'support' },
  'zammad': { svg: svg('zammad'), fallback: 'support_agent' },
  // Enterprise & Admin
  'sharepoint': { svg: svg('sharepoint'), fallback: 'share' },
  'sharepoint-online': { svg: svg('sharepoint'), fallback: 'share' },
  'google-admin': { svg: svg('google-admin'), fallback: 'admin_panel_settings' },
  'google-cloud': { svg: svg('gcs'), fallback: 'cloud' },
  'salesforce': { svg: svg('salesforce'), fallback: 'cloud' },
  // Databases
  'postgresql': { svg: svg('postgresql'), fallback: 'storage' },
  'mariadb': { svg: svg('mariadb'), fallback: 'storage' },
  'snowflake': { svg: svg('snowflake'), fallback: 'ac_unit' },
  'airtable': { svg: svg('airtable'), fallback: 'grid_view' },
  // Media & Other
  'youtube': { svg: svg('yt'), fallback: 'smart_display' },
  'rss': { svg: svg('rss'), fallback: 'rss_feed' },
  'seek': { svg: svg('seek'), fallback: 'search' },
  'frame': { svg: svg('frame'), fallback: 'frame_inspect' },
  'vector': { svg: svg('vector'), fallback: 'data_array' },
  'clickup': { svg: svg('clickup'), fallback: 'task_alt' },
  'redshift': { svg: svg('redshift'), fallback: 'storage' },
  // Generic / Fallback
  'web': { svg: svg('web'), fallback: 'language' },
  'generic': { svg: svg('default'), fallback: 'extension' },
};

/**
 * Ordered fuzzy-match rules: [pattern, ConnectorType].
 * More specific patterns come first to prevent false matches.
 * Patterns are matched via .includes() against normalized input.
 */
const FUZZY_MATCH_RULES: Array<[string, ConnectorType]> = [
  // Google suite — specific first (google-cloud-storage before google-cloud)
  ['google-cloud-storage', 'gcs'],
  ['google-drive', 'google-drive'], ['gdrive', 'google-drive'],
  ['google-docs', 'google-docs'], ['google-forms', 'google-forms'],
  ['google-meet', 'google-meet'], ['google-sheets', 'google-sheets'],
  ['google-slides', 'google-slides'], ['google-admin', 'google-admin'],
  ['google-cloud', 'google-cloud'], ['google-calendar', 'google-calendar'],
  ['gmail', 'gmail'],
  // Microsoft suite — specific first
  ['sharepointonline', 'sharepoint-online'], ['sharepoint-online', 'sharepoint-online'],
  ['sharepoint', 'sharepoint'],
  ['onedrive', 'onedrive'], ['outlook', 'outlook'],
  ['onenote', 'ms-onenote'], ['ms-note', 'ms-onenote'],
  ['teams', 'teams'],
  ['zoom', 'zoom'],
  // Cloud storage
  ['azure-files', 'azure-files'],
  ['amazon-s3', 'amazon-s3'], ['aws-s3', 'amazon-s3'], ['s3', 'amazon-s3'],
  ['azure-fileshare', 'azure-fileshares'], ['azure-blob', 'azure-blob'],
  ['azure-storage', 'azure-blob'],
  ['local-filesystem', 'local-fs'], ['local-fs', 'local-fs'], ['localfs', 'local-fs'],
  ['local-files', 'local-fs'],
  ['dropbox', 'dropbox'], ['box', 'box'],
  ['minio', 'minio'], ['nextcloud', 'nextcloud'],
  // Dev tools & project tracking
  ['github', 'github'],
  ['linear', 'linear'],
  ['jira', 'jira'], ['confluence', 'confluence'],
  ['gitlab', 'gitlab'], ['slack', 'slack'],
  ['servicenow', 'servicenow'], ['service-now', 'servicenow'],
  ['zendesk', 'zendesk'], ['zammad', 'zammad'],
  // Databases
  ['snowflake', 'snowflake'], ['postgresql', 'postgresql'], ['postgres', 'postgresql'],
  ['mariadb', 'mariadb'], ['airtable', 'airtable'],
  // Document & Knowledge
  ['notion', 'notion'], ['bookstack', 'bookstack'],
  // Media & Other
  ['youtube', 'youtube'], ['rss', 'rss'],
  ['seek', 'seek'], ['frame', 'frame'], ['vector', 'vector'],
  // Broad fallbacks (last — only match if nothing specific matched)
  ['google', 'google-drive'], ['microsoft', 'sharepoint'], ['365', 'sharepoint'],
  ['azure', 'azure-blob'], ['drive', 'google-drive'],
  ['calendar', 'google-calendar'],
  ['web', 'web'],
  ['clickup', 'clickup'], ['click-up', 'clickup'],
  ['redshift', 'redshift'], ['red-shift', 'redshift'],
  ['kb', 'kb'],
  ['knowledge-base', 'knowledge-base'],
];

/**
 * Resolve any arbitrary connector string to a ConnectorType.
 * Uses direct key match first, then fuzzy includes-based matching.
 */
export function resolveConnectorType(input: string): ConnectorType {
  if (!input) return 'generic';

  // Direct match
  if (input in CONNECTOR_ICONS) return input as ConnectorType;

  // Normalize: lowercase, replace spaces/underscores with hyphens
  const normalized = input.toLowerCase().replace(/[_\s]+/g, '-');
  if (normalized in CONNECTOR_ICONS) return normalized as ConnectorType;

  // Fuzzy match
  for (const [pattern, type] of FUZZY_MATCH_RULES) {
    if (normalized.includes(pattern)) return type;
  }

  return 'generic';
}

/**
 * Get icon config (svg path + material icon fallback) for any connector string.
 */
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

  // Use SVG if available and no error occurred
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

  // Fallback to Material Icon
  return (
    <MaterialIcon
      name={iconConfig.fallback}
      size={size}
      color={color}
      style={style}
    />
  );
};
