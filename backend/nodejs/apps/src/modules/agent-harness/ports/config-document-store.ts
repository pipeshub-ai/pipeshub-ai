/** One document in the deployment's configuration store. */
export interface ConfigDocument<T> {
  value: T | null;
  /** Opaque; pass back to `replace` for compare-and-set. null when absent. */
  revision: string | null;
}

/**
 * A single configuration document with compare-and-set, so replicas that
 * start together converge on one generated secret instead of overwriting.
 */
export interface ConfigDocumentStore<T> {
  read(): Promise<ConfigDocument<T>>;
  /** Writes only if the stored revision is still `expected`; false otherwise. */
  replace(expected: string | null, value: T): Promise<boolean>;
}
