import { Schema } from 'mongoose';

/** Adds fields and indexes to a schema before its model is compiled. */
export interface SchemaExtensions {
  chatSession(schema: Schema): void;
  chatSessionMessage(schema: Schema): void;
  project(schema: Schema): void;
  /** Session fields the edition adds that a list row leaves out unless the edition's own views show them. */
  readonly editionSessionFields: readonly string[];
}

export const noSchemaExtensions: SchemaExtensions = {
  chatSession: () => undefined,
  chatSessionMessage: () => undefined,
  project: () => undefined,
  editionSessionFields: [],
};

/** A list query's projection that keeps a row as the default edition stores it. */
export const defaultListSelect = (extensions: SchemaExtensions): string =>
  ['-__v', ...extensions.editionSessionFields.map((field) => `-${field}`)].join(
    ' ',
  );
