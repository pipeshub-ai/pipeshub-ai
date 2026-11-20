/**
 * Edition schema extensions, applied by the schema modules before their models compile.
 *
 * Kept import-light and separate from `config.ts`: `config.ts` re-exports routes that import the
 * schema modules, so a schema module importing it would cycle.
 */

export { noSchemaExtensions as schemaExtensions } from './modules/enterprise_search/schema/schema-extensions';
