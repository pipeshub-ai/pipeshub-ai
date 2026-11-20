'use client';

import { createContext } from 'react';

/**
 * The slot a reply is rendered for. It is the active slot unless a Compare
 * column renders a hidden branch's reply, whose cards must act on that branch.
 * Answer components read it through `useAnswerSlotId()` (answer-slot-scope.ts).
 */
export const RenderSlotContext = createContext<string | null>(null);
