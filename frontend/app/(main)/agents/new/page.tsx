'use client';

import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { Box } from '@radix-ui/themes';
import { AgentBuilder } from '@/app/(main)/agents/agent-builder/agent-builder';
import { ServiceGate } from '@/app/components/ui/service-gate';
import { CreateAgentDialog } from '@/app/(main)/agents/components/create-agent-dialog';

export default function NewAgentPage() {
  const router = useRouter();
  const [dialogOpen, setDialogOpen] = useState(true);

  useEffect(() => {
    setDialogOpen(true);
  }, []);

  const handleOpenChange = (open: boolean) => {
    setDialogOpen(open);
    if (!open) {
      router.back();
    }
  };

  return (
    <ServiceGate services={['query']}>
      <Box style={{ height: '100%', minHeight: 0, display: 'flex', flexDirection: 'column' }}>
        <CreateAgentDialog open={dialogOpen} onOpenChange={handleOpenChange} />
        <AgentBuilder agentKey={null} />
      </Box>
    </ServiceGate>
  );
}
