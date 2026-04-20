// External connector links for All Records page

import type { MoreConnectorLink } from './types';

// More connectors links (external links to other connector pages)
export const MORE_CONNECTORS: MoreConnectorLink[] = [
  {
    id: 'dropbox-link',
    name: 'Dropbox',
    type: 'dropbox',
    icon: 'cloud_upload',
    url: '/connectors/dropbox',
    isExternal: true,
  },
  {
    id: 'notion-link',
    name: 'Notion',
    type: 'notion',
    icon: 'language', // Valid Material Icon fallback (not used since ConnectorIcon uses type)
    url: '/connectors/notion',
    isExternal: true,
  },
  {
    id: 'slack-link',
    name: 'Slack',
    type: 'slack',
    icon: 'tag', // Valid Material Icon fallback (not used since ConnectorIcon uses type)
    url: '/connectors/slack',
    isExternal: true,
  }
];
