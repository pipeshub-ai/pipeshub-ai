import type { FlowNodeData } from './types';

/** Used when no collection artwork is available (legacy agent builder parity). */
export const AGENT_KNOWLEDGE_FALLBACK_ICON = '/assets/icons/connectors/collections-gray.svg';

/** Neutral connector glyph when a tool/app connector asset is missing or fails to load. */
export const AGENT_TOOLSET_FALLBACK_ICON = '/assets/icons/connectors/default.svg';

function isIconPathString(s: string): boolean {
  const t = s.trim();
  return t.startsWith('/') || t.startsWith('http');
}

/** Maps a display name or id fragment to `/assets/icons/connectors/<slug>.svg` (toolset / tool rows). */
function connectorIconPathFromLabel(raw: string): string | undefined {
  const slug = raw
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return slug ? `/assets/icons/connectors/${slug}.svg` : undefined;
}

function isAppConnectorNodeType(type: string): boolean {
  return type.startsWith('app-') && !type.startsWith('app-group');
}

/**
 * `<img onError>` replacement: collection gray for KB nodes, neutral connector default elsewhere.
 */
export function resolveNodeHeaderIconErrorFallback(data: FlowNodeData): string {
  const { type } = data;
  if (type.startsWith('kb-') && type !== 'kb-group') return AGENT_KNOWLEDGE_FALLBACK_ICON;
  return AGENT_TOOLSET_FALLBACK_ICON;
}

/**
 * URL/path for flow node headers and agent-core chips.
 * Order: `config.iconPath`, URL-like `data.icon`, connector-specific rules (`app-*`, `kb-*`, toolset/tool).
 */
export function resolveNodeHeaderIconUrl(data: FlowNodeData): string | undefined {
  const cfg = (data.config || {}) as Record<string, unknown>;
  const fromConfig = typeof cfg.iconPath === 'string' ? cfg.iconPath.trim() : '';
  if (fromConfig && isIconPathString(fromConfig)) return fromConfig;

  const fromDataIcon = typeof data.icon === 'string' ? data.icon.trim() : '';
  if (fromDataIcon && isIconPathString(fromDataIcon)) return fromDataIcon;

  if (isAppConnectorNodeType(data.type)) {
    const connectorType =
      typeof cfg.connectorType === 'string' ? cfg.connectorType.toLowerCase().replace(/\s+/g, '') : '';
    if (connectorType) return `/assets/icons/connectors/${connectorType}.svg`;
    const slug = data.type.slice(4).toLowerCase().replace(/\s+/g, '');
    if (slug) return `/assets/icons/connectors/${slug}.svg`;
  }

  if (data.type.startsWith('kb-') && data.type !== 'kb-group') {
    return AGENT_KNOWLEDGE_FALLBACK_ICON;
  }

  if (data.type.startsWith('toolset-')) {
    const name =
      (typeof cfg.toolsetName === 'string' && cfg.toolsetName.trim()) ||
      data.type.slice('toolset-'.length);
    return connectorIconPathFromLabel(name);
  }

  if (data.type.startsWith('tool-group-')) {
    const appName =
      (typeof cfg.appName === 'string' && cfg.appName.trim()) || data.type.slice('tool-group-'.length);
    return connectorIconPathFromLabel(appName);
  }

  if (data.type.startsWith('tool-') && !data.type.startsWith('tool-group-')) {
    const appName = typeof cfg.appName === 'string' ? cfg.appName.trim() : '';
    if (appName) return connectorIconPathFromLabel(appName);
  }

  return undefined;
}

export function normalizeDisplayName(name: string): string {
  return name
    .split('_')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(' ');
}

export function formattedProvider(provider: string): string {
  switch (provider) {
    case 'azureOpenAI':
      return 'Azure OpenAI';
    case 'openAI':
      return 'OpenAI';
    case 'anthropic':
      return 'Anthropic';
    case 'gemini':
      return 'Gemini';
    case 'ollama':
      return 'Ollama';
    case 'bedrock':
      return 'AWS Bedrock';
    case 'xai':
      return 'xAI';
    case 'groq':
      return 'Groq';
    case 'mistral':
      return 'Mistral';
    case 'openAICompatible':
      return 'OpenAI API Compatible';
    default:
      return provider || 'AI';
  }
}

export function truncateText(text: string, maxLength = 50): string {
  if (text.length <= maxLength) return text;
  return `${text.substring(0, maxLength)}…`;
}

export function getAppDisplayName(appName: string): string {
  return normalizeDisplayName(appName.replace(/\./g, '_'));
}

/** Material icon name for tool rows in sidebar */
export function getAppIconName(appName: string): string {
  const key = appName.toLowerCase();
  if (key.includes('slack')) return 'chat';
  if (key.includes('gmail') || key.includes('mail')) return 'mail';
  if (key.includes('drive')) return 'cloud';
  if (key.includes('jira')) return 'assignment';
  if (key.includes('github')) return 'code';
  if (key.includes('confluence')) return 'description';
  return 'extension';
}
