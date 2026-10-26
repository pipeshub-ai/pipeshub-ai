import { describe, expect, it } from 'vitest';
import { filterBarChatScope } from '../filter-bar-chat-scope';

const SOURCES = [
  { id: 'app-jira', label: 'Jira', connectorType: 'JIRA' },
  { id: 'kb-eng', label: 'Engineering', connectorType: 'KB' },
  { id: 'kb-sales', label: 'Sales', connectorType: 'COLLECTION' },
];

describe('filterBarChatScope', () => {
  it('hands no scope when the bar does not narrow the sources', () => {
    expect(filterBarChatScope({ recordTypes: ['FILE'], indexingStatus: ['FAILED'] }, SOURCES)).toEqual([]);
    expect(filterBarChatScope({ origins: ['COLLECTION', 'CONNECTOR'] }, SOURCES)).toEqual([]);
  });

  it('scopes to the chosen connectors and collections, named and typed for the chat', () => {
    expect(filterBarChatScope({ connectorIds: ['app-jira', 'kb-eng'] }, SOURCES)).toEqual([
      { id: 'app-jira', name: 'Jira', nodeType: 'app', connector: 'JIRA' },
      { id: 'kb-eng', name: 'Engineering', nodeType: 'app', connector: 'KB' },
    ]);
  });

  it('scopes a source filter to every source of that kind', () => {
    expect(filterBarChatScope({ origins: ['COLLECTION'] }, SOURCES).map((n) => n.id)).toEqual(['kb-eng', 'kb-sales']);
    expect(filterBarChatScope({ origins: ['CONNECTOR'] }, SOURCES).map((n) => n.id)).toEqual(['app-jira']);
  });

  it('keeps only the chosen connectors of the chosen kind, and a chosen one it cannot place', () => {
    const scope = filterBarChatScope({ connectorIds: ['app-jira', 'kb-eng', 'gone'], origins: ['COLLECTION'] }, SOURCES);
    expect(scope.map((n) => n.id)).toEqual(['kb-eng', 'gone']);
  });
});
