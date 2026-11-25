'use client';

import { HarnessOnly } from '@/app/components/harness-gate/harness-only';
import { OrgAiAccess } from './components/org-ai-access';

export default function AiAccessPage() {
  return (
    <HarnessOnly>
      <OrgAiAccess />
    </HarnessOnly>
  );
}
