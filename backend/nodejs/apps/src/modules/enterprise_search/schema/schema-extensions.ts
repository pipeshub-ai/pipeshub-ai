import { Schema } from 'mongoose';

/** Adds fields and indexes to a schema before its model is compiled. */
export interface SchemaExtensions {
  chatSession(schema: Schema): void;
  chatSessionMessage(schema: Schema): void;
  project(schema: Schema): void;
}

export const noSchemaExtensions: SchemaExtensions = {
  chatSession: () => undefined,
  chatSessionMessage: () => undefined,
  project: () => undefined,
};
