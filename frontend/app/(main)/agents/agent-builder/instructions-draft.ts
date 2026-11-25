/**
 * Instructions drafted in a chat by `/init` (SLASH2 C4, O4), handed to the
 * agent editor once. The editor opens them in the prompts dialog; nothing is
 * saved until the person saves there and then saves the agent.
 */
import { create } from 'zustand';

interface InstructionsDraftState {
  draft: { agentKey: string; text: string } | null;
  offer: (agentKey: string, text: string) => void;
  /** The draft for this agent, once; null when there is none. */
  take: (agentKey: string) => string | null;
}

export const useInstructionsDraft = create<InstructionsDraftState>((set, get) => ({
  draft: null,
  offer: (agentKey, text) => set({ draft: { agentKey, text } }),
  take: (agentKey) => {
    const draft = get().draft;
    if (draft === null || draft.agentKey !== agentKey) return null;
    set({ draft: null });
    return draft.text;
  },
}));

/** The instructions in an `/init` answer: its first ```text block, else the whole answer. */
export function instructionsFromAnswer(answer: string): string {
  const fenced = /```text[^\S\n]*\n([\s\S]*?)```/.exec(answer);
  return (fenced ? fenced[1] : answer).trim();
}
