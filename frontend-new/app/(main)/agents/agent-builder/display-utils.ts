import type { FlowNodeData } from './types';
import {
  AGENT_LLM_FALLBACK_ICON,
  AGENT_LLM_ICONS_BASE,
  resolveLlmProviderIconPath,
} from '@/lib/utils/llm-provider-icons';

export { AGENT_LLM_FALLBACK_ICON, AGENT_LLM_ICONS_BASE, resolveLlmProviderIconPath };

/** Used when no collection artwork is available (legacy agent builder parity). */
export const AGENT_KNOWLEDGE_FALLBACK_ICON = '/icons/connectors/collections-gray.svg';

/** Neutral connector glyph when a tool/app connector asset is missing or fails to load. */
export const AGENT_TOOLSET_FALLBACK_ICON = '/icons/connectors/default.svg';

function isIconPathString(s: string): boolean {
  const t = s.trim();
  return t.startsWith('/') || t.startsWith('http');
}

/** Maps a display name or id fragment to `/icons/connectors/<slug>.svg` (toolset / tool rows). */
function connectorIconPathFromLabel(raw: string): string | undefined {
  const slug = raw
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return slug ? `/icons/connectors/${slug}.svg` : undefined;
}

function isAppConnectorNodeType(type: string): boolean {
  return type.startsWith('app-') && !type.startsWith('app-group');
}

/** Icon URL used when the primary asset fails to load (aligned with `resolveNodeHeaderIconUrl`). */
export function resolveNodeHeaderIconErrorFallback(data: FlowNodeData): string {
  const { type } = data;
  if (type.startsWith('llm-')) return AGENT_LLM_FALLBACK_ICON;
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

  if (data.type.startsWith('llm-')) {
    const raw = typeof cfg.provider === 'string' ? cfg.provider.trim() : '';
    return resolveLlmProviderIconPath(raw || undefined);
  }

  if (isAppConnectorNodeType(data.type)) {
    const connectorType =
      typeof cfg.connectorType === 'string' ? cfg.connectorType.toLowerCase().replace(/\s+/g, '') : '';
    if (connectorType) return `/icons/connectors/${connectorType}.svg`;
    const slug = data.type.slice(4).toLowerCase().replace(/\s+/g, '');
    if (slug) return `/icons/connectors/${slug}.svg`;
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

/**
 * Connector type slug for nodes whose icon comes from a connector brand SVG —
 * `app-*` (non-group) and `toolset-*`. Used to render `ConnectorIcon`, which
 * applies dark-mode inversion for brand SVGs that `ThemeableAssetIcon` can't
 * theme (it only handles `currentColor` SVGs; brand SVGs fall through to a
 * plain `<img>`). Returns `null` for LLM, KB, and other node kinds.
 */
export function resolveNodeConnectorType(data: FlowNodeData): string | null {
  const cfg = (data.config || {}) as Record<string, unknown>;
  const type = data.type;
  console.log('resolveNodeConnectorType', type);
  console.log("config", cfg);

  if (isAppConnectorNodeType(type)) {
    const fromCfg = typeof cfg.connectorType === 'string' ? cfg.connectorType.trim() : '';
    return fromCfg || type.slice('app-'.length) || null;
  }
  if (type.startsWith('toolset-')) {
    const fromCfg = typeof cfg.toolsetName === 'string' ? cfg.toolsetName.trim() : '';
    return fromCfg || type.slice('toolset-'.length) || null;
  }
  return null;
}

export function normalizeDisplayName(name: string): string {
  return name
    .split('_')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(' ');
}

/**
 * Palette / sidebar titles for toolsets and similar ids: matches canvas nodes
 * (`normalizeDisplayName`) for snake_case, kebab-case, and dotted slugs; leaves
 * human-readable phrases that already contain spaces unchanged.
 */
export function normalizePaletteLabel(raw: string): string {
  const s = (raw || '').trim();
  if (!s) return '';
  const noSpaces = !/\s/.test(s);
  const snakeOrKebab = /[_\-.]/.test(s);
  const compactAlnum = /^[a-z0-9]+$/i.test(s);
  const looksLikeSlug = (snakeOrKebab && noSpaces) || (compactAlnum && noSpaces);
  if (!looksLikeSlug) return s;
  return normalizeDisplayName(s.replace(/-/g, '_').replace(/\./g, '_'));
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
