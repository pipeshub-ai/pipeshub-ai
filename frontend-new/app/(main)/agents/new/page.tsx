'use client';

import { Box } from '@radix-ui/themes';
import { AgentBuilder } from '@/app/(main)/agents/agent-builder/agent-builder';

export default function NewAgentPage() {
  return (
    <Box style={{ height: '100%', minHeight: 0, display: 'flex', flexDirection: 'column' }}>
      <AgentBuilder agentKey={null} />
    </Box>
  );
}
