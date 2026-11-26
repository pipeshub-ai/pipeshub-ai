/** Same exports as the file this one replaces; nothing is read, shown or saved. */

const NOTHING = {
  loaded: true,
  error: undefined as unknown,
  validating: false,
  reread: (): unknown => undefined,
  value: false,
  save: (_next: boolean, _saved: boolean): Promise<void> => Promise.resolve(),
};

export function useDataCollection() {
  return NOTHING;
}

export function DataCollectionSection() {
  return null;
}
