import { create } from 'zustand';

interface AddPeopleState {
  /** Set while the share drawer is open for a request that came from the @ list. */
  query: string | null;
  /** Bumped when that drawer closes, so the composer can take focus back and refresh its lists. */
  closedTick: number;
  request(query: string): void;
  finish(): void;
}

/** The @ list asks the chat page to open its share drawer; the composer and the page never import each other. */
export const useAddPeopleStore = create<AddPeopleState>((set) => ({
  query: null,
  closedTick: 0,
  request: (query) => set({ query }),
  finish: () => set((s) => (s.query === null ? s : { query: null, closedTick: s.closedTick + 1 })),
}));
