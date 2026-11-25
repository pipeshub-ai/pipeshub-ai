import type { ChangeEvent } from '../contracts/generated';

export type Unsubscribe = () => void;

/**
 * Publishes committed changes to every process (03-interfaces §5.4): ordered
 * per org, at least once. `publish` is called by the outbox dispatcher only; a
 * late subscriber gets no replay and recovers by the gap rule.
 */
export interface ChangeNotifier {
  publish(event: ChangeEvent): Promise<void>;
  subscribe(handler: (event: ChangeEvent) => void): Unsubscribe;
}
