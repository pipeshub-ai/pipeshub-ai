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
export const CONNECTOR_ICONS: Record<ConnectorType, { svg: string | null; fallback: string; needDarkModeInvert?: boolean }> = {
  // Communication & Collaboration
  'slack': { svg: '/icons/connectors/slack.svg', fallback: 'tag' },
  'teams': { svg: '/icons/connectors/teams.svg', fallback: 'groups' },
  'zoom': { svg: '/icons/connectors/zoom.svg', fallback: 'videocam' },
  'gmail': { svg: '/icons/connectors/gmail.svg', fallback: 'mail' },
  'outlook': { svg: '/icons/connectors/outlook.svg', fallback: 'mail' },
  'google-meet': { svg: '/icons/connectors/meet.svg', fallback: 'videocam' },
  'google-calendar': { svg: '/icons/connectors/calendar.svg', fallback: 'calendar_today' },
  // Cloud Storage & Drives
  'google-drive': { svg: '/icons/connectors/drive.svg', fallback: 'cloud' },
  'onedrive': { svg: '/icons/connectors/onedrive.svg', fallback: 'cloud_upload' },
  'dropbox': { svg: '/icons/connectors/dropbox.svg', fallback: 'cloud_upload' },
  'box': { svg: '/icons/connectors/box.svg', fallback: 'inventory_2' },
  'amazon-s3': { svg: '/icons/connectors/s3.svg', fallback: 'cloud' },
  'gcs': { svg: '/icons/connectors/gcs.svg', fallback: 'cloud' },
  'minio': { svg: '/icons/connectors/minio.svg', fallback: 'cloud' },
  'azure-blob': { svg: '/icons/connectors/azureblob.svg', fallback: 'cloud' },
  'azure-files': { svg: '/icons/connectors/azurefiles.svg', fallback: 'folder_shared' },
  'azure-fileshares': { svg: '/icons/connectors/azure-files.svg', fallback: 'folder_shared' },
  'nextcloud': { svg: '/icons/connectors/nextcloud.svg', fallback: 'cloud' },
  // Document & Knowledge
  'notion': { svg: '/icons/connectors/notion.svg', fallback: 'description', needDarkModeInvert: true },
  'confluence': { svg: '/icons/connectors/confluence.svg', fallback: 'article' },
  'bookstack': { svg: '/icons/connectors/bookstack.svg', fallback: 'menu_book' },
  'google-docs': { svg: '/icons/connectors/docs.svg', fallback: 'description' },
  'google-sheets': { svg: '/icons/connectors/sheets.svg', fallback: 'table_chart' },
  'google-slides': { svg: '/icons/connectors/slides.svg', fallback: 'slideshow' },
  'google-forms': { svg: '/icons/connectors/forms.svg', fallback: 'quiz' },
  'ms-onenote': { svg: '/icons/connectors/ms-onenote.svg', fallback: 'note' },
  'kb': { svg: '/icons/connectors/kb.svg', fallback: 'folder' },
  'knowledge-base': { svg: '/icons/connectors/kb.svg', fallback: 'folder' },
  // Project & Issue Tracking (keys sorted A–Z)
  'github': { svg: '/icons/connectors/github.svg', fallback: 'code', needDarkModeInvert: true },
  'gitlab': { svg: '/icons/connectors/gitlab.svg', fallback: 'code' },
  'jira': { svg: '/icons/connectors/jira.svg', fallback: 'bug_report' },
  'linear': { svg: '/icons/connectors/linear.svg', fallback: 'view_kanban', needDarkModeInvert: true },
  'servicenow': { svg: '/icons/connectors/servicenow.svg', fallback: 'build' },
  'zendesk': { svg: '/icons/connectors/zendesk.svg', fallback: 'support' },
  'zammad': { svg: '/icons/connectors/zammad.svg', fallback: 'support_agent' },
  // Enterprise & Admin
  'sharepoint': { svg: '/icons/connectors/sharepoint.svg', fallback: 'share' },
  'sharepoint-online': { svg: '/icons/connectors/sharepoint.svg', fallback: 'share' },
  'google-admin': { svg: '/icons/connectors/google-admin.svg', fallback: 'admin_panel_settings' },
  'google-cloud': { svg: '/icons/connectors/gcs.svg', fallback: 'cloud' },
  'salesforce': { svg: '/icons/connectors/salesforce.svg', fallback: 'cloud' },
  // Databases
  'postgresql': { svg: '/icons/connectors/postgresql.svg', fallback: 'storage' },
  'mariadb': { svg: '/icons/connectors/mariadb.svg', fallback: 'storage' },
  'snowflake': { svg: '/icons/connectors/snowflake.svg', fallback: 'ac_unit' },
  'airtable': { svg: '/icons/connectors/airtable.svg', fallback: 'grid_view' },
  // Media & Other
  'youtube': { svg: '/icons/connectors/yt.svg', fallback: 'smart_display' },
  'rss': { svg: '/icons/connectors/rss.svg', fallback: 'rss_feed' },
  'seek': { svg: '/icons/connectors/seek.svg', fallback: 'search' },
  'frame': { svg: '/icons/connectors/frame.svg', fallback: 'frame_inspect' },
  'vector': { svg: '/icons/connectors/vector.svg', fallback: 'data_array' },
  'clickup': { svg: '/icons/connectors/clickup.svg', fallback: 'task_alt' },
  'redshift': { svg: '/icons/connectors/redshift.svg', fallback: 'storage' },
  // Generic / Fallback
  'web': { svg: '/icons/connectors/web.svg', fallback: 'language' },
  'generic': { svg: '/icons/connectors/default.svg', fallback: 'extension' },
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
          filter: isDarkMode && (iconConfig.needDarkModeInvert ?? false) ? 'invert(1)' : undefined,
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
