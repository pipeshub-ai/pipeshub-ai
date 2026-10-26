import { MongoDocumentRepository } from './mongo';
import type { DocumentRepository } from './types';

export type * from './types';
export { ENVELOPE_PREFIXES } from './types';

let repo: DocumentRepository | undefined;

export const getDocumentRepository = (): DocumentRepository =>
  (repo ??= new MongoDocumentRepository());

/** Tests: install a fake, or call with no argument to restore the default. */
export const setDocumentRepository = (r?: DocumentRepository): void => {
  repo = r;
};
