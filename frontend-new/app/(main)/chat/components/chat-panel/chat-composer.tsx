'use client';

import React from 'react';
import { ComposerPrimitive } from '@assistant-ui/react';
import { ChatInput } from '../chat-input';
import type { UploadedFile } from '@/chat/types';

export function ChatComposer() {
  return (
    <ComposerPrimitive.Root>
      <ChatInput
        onSend={(_message: string, _files?: UploadedFile[]) => {
          // Wired through ChatInputWrapper in the live chat surface; this
          // legacy composer wrapper remains a no-op since assistant-ui's
          // runtime handles message dispatch elsewhere.
        }}
      />
    </ComposerPrimitive.Root>
  );
}
