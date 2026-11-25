/**
 * WP103-FU-1: what the run's gateways hid from its model calls, as the one plain activity line its owner reads
 * ("Hid 2 secret-shaped values from the model"). Projected from the `gateway.secret_hidden` audit events, which stay
 * as they are: the kinds only, once each however many gateways reported it, never the value, a fragment or where it
 * was found.
 */
export const SECRET_HIDDEN = 'gateway.secret_hidden';

/** `gateway.secret_hidden`'s `kind` (core/events/gateway-secret-hidden.schema.json). */
const KIND = /^[a-z0-9][a-z0-9_.-]{0,63}$/;

export interface SecretsHiddenView {
  /** The credential shapes hidden, in the order they were first reported, each once. */
  kinds: string[];
  /** How many shapes: `kinds.length`, so a client words the line without counting. */
  count: number;
}

/** `view` with the kind one more `gateway.secret_hidden` names; a malformed record or a kind already seen adds none. */
export function withSecretHidden(
  view: SecretsHiddenView | undefined,
  data: Record<string, unknown>,
): SecretsHiddenView | undefined {
  const kind = data['kind'];
  if (typeof kind !== 'string' || !KIND.test(kind)) return view;
  if (view?.kinds.includes(kind)) return view;
  const kinds = [...(view?.kinds ?? []), kind];
  return { kinds, count: kinds.length };
}
