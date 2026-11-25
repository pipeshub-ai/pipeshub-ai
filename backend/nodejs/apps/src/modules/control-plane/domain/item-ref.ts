import { InvalidItemRef } from './catalog-errors';

/** `{kind}:{publisher}/{name}@{version}` (02-lld §1.3); the pattern of `ItemRef` in common.schema.json. */
export const ITEM_REF_PATTERN =
  /^([a-z][a-z0-9-]{0,31}):([a-z0-9][a-z0-9._-]{0,63})\/([a-z0-9][a-z0-9._-]{0,127})@([0-9A-Za-z][0-9A-Za-z.+-]{0,63})$/;

export interface ItemRefParts {
  readonly kind: string;
  readonly publisher: string;
  readonly name: string;
  readonly version: string;
}

export function parseItemRef(ref: string): ItemRefParts {
  const match = ITEM_REF_PATTERN.exec(ref);
  if (match === null)
    throw new InvalidItemRef(`not an item ref: ${JSON.stringify(ref)}`);
  const [, kind, publisher, name, version] = match as unknown as [
    string,
    string,
    string,
    string,
    string,
  ];
  return { kind, publisher, name, version };
}

export function formatItemRef(parts: ItemRefParts): string {
  const ref = `${parts.kind}:${parts.publisher}/${parts.name}@${parts.version}`;
  parseItemRef(ref);
  return ref;
}
