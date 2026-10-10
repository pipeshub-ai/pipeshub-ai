'use client';

import { Avatar, Flex, Text, Tooltip } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { RespondingAgent } from '../../types';

interface AgentAnswerHeaderProps {
  agent: RespondingAgent;
}

/** Names the guest agent that answered a turn. An agent the viewer cannot read arrives as a bare key and shows generically. */
export function AgentAnswerHeader({ agent }: AgentAnswerHeaderProps) {
  const { t } = useTranslation();
  const name = agent.name?.trim() || t('chat.collab.attribution.respondingAgent');
  const handle = agent.handle?.trim();
  const row = (
    <Flex
      align="center"
      gap="2"
      role="group"
      data-testid="agent-answer-header"
      aria-label={t('chat.collab.attribution.respondingAgentAria', { name })}
      style={{ marginBottom: 'var(--space-2)', minWidth: 0 }}
    >
      <span data-testid="agent-answer-avatar" style={{ display: 'inline-flex', flexShrink: 0 }}>
        <Avatar size="1" radius="full" variant="soft" color="jade" fallback={Array.from(name)[0]?.toUpperCase() ?? '?'} />
      </span>
      <Text
        size="2"
        weight="medium"
        data-testid="agent-answer-name"
        style={{ color: 'var(--slate-12)', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
      >
        {name}
      </Text>
    </Flex>
  );
  return handle ? <Tooltip content={`@${handle}`}>{row}</Tooltip> : row;
}
