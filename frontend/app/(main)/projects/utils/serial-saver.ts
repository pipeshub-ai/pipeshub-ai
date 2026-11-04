/**
 * Saves a value that changes faster than the server answers (e.g. a project's
 * whole tool list on every checkbox click). One request runs at a time; values
 * pushed meanwhile collapse into a single follow-up with the latest one, so
 * the server always ends on the last value pushed. `onSaved`/`onError` fire
 * only for the newest value: an older response must never overwrite a newer
 * local change.
 */
export interface SerialSaver<T> {
  push: (value: T) => Promise<void>;
  isSaving: () => boolean;
}

export function createSerialSaver<T, R>({
  save,
  onSaved,
  onError,
}: {
  save: (value: T) => Promise<R>;
  onSaved: (result: R, value: T) => void;
  onError: (error: unknown, value: T) => void;
}): SerialSaver<T> {
  let latest: T;
  let pending = false;
  let running: Promise<void> | null = null;

  const drain = async () => {
    while (pending) {
      pending = false;
      const value = latest;
      try {
        const result = await save(value);
        if (!pending) onSaved(result, value);
      } catch (error) {
        if (!pending) onError(error, value);
      }
    }
  };

  const start = (): Promise<void> => {
    running = drain().finally(() => {
      running = null;
      // A push that landed after the loop's last check but before this ran.
      if (pending) void start();
    });
    return running;
  };

  return {
    push(value: T) {
      latest = value;
      pending = true;
      return running ?? start();
    },
    isSaving: () => running !== null,
  };
}
