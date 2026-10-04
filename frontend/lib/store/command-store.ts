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
 * If nothing is registered, `dispatch` returns false and remembers the
 * command. The next `register` for that name runs immediately — so New Chat
 * from /artifacts/ can navigate to /chat/ and still reset the thread.
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
  /** Commands dispatched before a page registered a handler. */
  pending: Record<string, boolean>;

  /** Register a named command handler. Overwrites any previous handler for the same name. */
  register: (name: string, handler: CommandHandler) => void;

  /** Unregister a named command handler. */
  unregister: (name: string) => void;

  /**
   * Run the registered handler. Returns false when none is mounted so the
   * caller can navigate to a page that will register and flush the command.
   */
  dispatch: (name: string, payload?: unknown) => boolean;
}

export const useCommandStore = create<CommandState>((set, get) => ({
  handlers: {},
  pending: {},

  register: (name, handler) => {
    const shouldFlush = Boolean(get().pending[name]);
    set((state) => {
      const pending = { ...state.pending };
      delete pending[name];
      return {
        handlers: { ...state.handlers, [name]: handler },
        pending,
      };
    });
    if (shouldFlush) handler();
  },

  unregister: (name) =>
    set((state) => {
      const { [name]: _, ...rest } = state.handlers;
      return { handlers: rest };
    }),

  dispatch: (name, payload) => {
    const handler = get().handlers[name];
    if (handler) {
      handler(payload);
      return true;
    }
    set((state) => ({
      pending: { ...state.pending, [name]: true },
    }));
    return false;
  },
}));
