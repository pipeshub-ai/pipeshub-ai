'use client';

import { HarnessOnly } from '@/app/components/harness-gate/harness-only';
import { MyAiAccounts } from './components/my-ai-accounts';

export default function AiAccountsPage() {
  return (
    <HarnessOnly>
      <MyAiAccounts />
    </HarnessOnly>
  );
}
