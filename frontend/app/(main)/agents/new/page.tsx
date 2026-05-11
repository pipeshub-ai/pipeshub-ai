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
  const [isLeaving, setIsLeaving] = useState(false);

  useEffect(() => {
    setDialogOpen(true);
  }, []);

  const handleOpenChange = (open: boolean) => {
    if (!open) {
      // Fade the whole page out together (dialog + background),
      // then navigate once the animation finishes.
      setIsLeaving(true);
      setTimeout(() => router.push('/chat'), 200);
    } else {
      setDialogOpen(open);
    }
  };

  return (
    <ServiceGate services={['query']}>
      <Box style={{ height: '100%', minHeight: 0, display: 'flex', flexDirection: 'column', 
                    opacity: isLeaving ? 0 : 1, transition: 'opacity 0.2s ease', }}>
        <CreateAgentDialog open={dialogOpen} onOpenChange={handleOpenChange} />
        <AgentBuilder agentKey={null} />
      </Box>
    </ServiceGate>
  );
}
