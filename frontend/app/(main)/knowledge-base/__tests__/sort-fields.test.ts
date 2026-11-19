import { describe, expect, it } from 'vitest';
import { ALL_RECORDS_SORT_FIELDS, SORT_FIELDS } from '../types';
import contract from '../sort-fields.json';

// The backend checks this file against the API's SortField enum; this keeps it
// equal to the lists the page actually sends as `sortBy`.
describe('sort fields the page sends', () => {
  it('are the ones sort-fields.json declares to the backend', () => {
    expect({ collections: [...SORT_FIELDS], allRecords: [...ALL_RECORDS_SORT_FIELDS] }).toEqual(contract);
  });
});
