import { create } from 'zustand';

/**
 * Global command bus — a lightweight publish/subscribe registry for
 * named actions that any component can trigger and any page can handle.
 *
 * **Architecture:**
 * - Pages register handlers on mount via `register('commandName', handler)`
 *   and unregister on unmount via `unregister('commandName')`.
 * - UI elements (buttons, keyboard shortcuts) call `dispatch('commandName')`
 *   to execute the currently-registered handler.
 * - This decouples the *trigger* ("CMD+N pressed", "plus button clicked")
 *   from the *action* ("navigate to /chat", "reset thread") so multiple
 *   entry points share a single implementation without prop-drilling.
 *
 * **Example — new chat:**
 * ```tsx
 * // In ChatPage (registers handler):
 * const { register, unregister } = useCommandStore();
 * useEffect(() => {
 *   register('newChat', () => router.push('/chat'));
 *   return () => unregister('newChat');
 * }, []);
 *
 * // In any button / shortcut listener:
 * useCommandStore.getState().dispatch('newChat');
 * ```
 */

type CommandHandler = (payload?: unknown) => void;

interface CommandState {
  /** Internal handler registry — keyed by command name */
  handlers: Record<string, CommandHandler>;

  /** Register a named command handler. Overwrites any previous handler for the same name. */
  register: (name: string, handler: CommandHandler) => void;

  /** Unregister a named command handler. */
  unregister: (name: string) => void;

  /** Dispatch (execute) a named command with an optional payload. No-op if no handler is registered. */
  dispatch: (name: string, payload?: unknown) => void;
}

export const useCommandStore = create<CommandState>((set, get) => ({
  handlers: {},

  register: (name, handler) =>
    set((state) => ({
      handlers: { ...state.handlers, [name]: handler },
    })),

  unregister: (name) =>
    set((state) => {
      const { [name]: _, ...rest } = state.handlers;
      return { handlers: rest };
    }),

  dispatch: (name, payload) => {
    const handler = get().handlers[name];
    if (handler) handler(payload);
  },
}));
