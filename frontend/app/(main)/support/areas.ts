'use client';

import { SUPPORT_AREA_KEYS, type SupportAreaKey } from './types';

interface AreaMeta {
  icon: string;
  /** API path prefixes whose errors suggest this area in step 1. */
  apiPrefixes: string[];
}

export const SUPPORT_AREA_META: Record<SupportAreaKey, AreaMeta> = {
  connectors: { icon: 'hub', apiPrefixes: ['/api/v1/connectors', '/api/v1/connector'] },
  chat: { icon: 'search', apiPrefixes: ['/api/v1/conversations', '/api/v1/chat', '/api/v1/search'] },
  agents: { icon: 'smart_toy', apiPrefixes: ['/api/v1/agents', '/api/v1/toolsets'] },
  files: { icon: 'folder', apiPrefixes: ['/api/v1/knowledgeBase', '/api/v1/document'] },
  access: { icon: 'shield', apiPrefixes: ['/api/v1/orgAuthConfig'] },
  people: { icon: 'group', apiPrefixes: ['/api/v1/users', '/api/v1/userGroups'] },
  other: { icon: 'help', apiPrefixes: [] },
};

export function areaForPath(path: string): SupportAreaKey | null {
  return (
    SUPPORT_AREA_KEYS.find((area) =>
      SUPPORT_AREA_META[area].apiPrefixes.some((prefix) => path.startsWith(prefix)),
    ) ?? null
  );
}
