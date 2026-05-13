'use client';

import React from 'react';
import { ComposerPrimitive } from '@assistant-ui/react';
import { ChatInput } from '../chat-input';
import type { AttachmentRef } from '@/chat/types';

export function ChatComposer() {
  return (
    <ComposerPrimitive.Root>
      <ChatInput
        onSend={(_message: string, _attachments?: AttachmentRef[]) => {
          // The message will be sent through the runtime adapter
          // This integration allows us to keep the existing ChatInput UI
          // while using assistant-ui's runtime for message handling
        }}
      />
    </ComposerPrimitive.Root>
  );
}
