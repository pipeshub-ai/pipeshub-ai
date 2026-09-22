/**
 * Pure helpers behind the project-scoped composer: tool-key normalisation, catalog group
 * builders, and the projection of a project's settings onto the composer allow-list.
 */
import { describe, it, expect, vi } from 'vitest';

vi.mock('@/lib/store/auth-store', () => ({
  useAuthStore: { getState: () => ({ isHydrated: true }) },
  hydrateAuthStore: vi.fn(),
  LOGIN_NAVIGATION_EVENT: 'pipeshub:request-login-navigation',
}));

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn(), request: vi.fn() },
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  axiosFetcher: vi.fn(),
  publicFetcher: vi.fn(),
  configuredFetcher: vi.fn(),
  streamRequest: vi.fn(),
  createStreamController: vi.fn(),
  streamSSERequest: vi.fn(),
  processError: vi.fn(),
  ErrorType: {},
  isProcessedError: vi.fn(() => false),
  isRequestCancelledError: vi.fn(() => false),
  isSearchNoAccessibleDocumentsNotFound: vi.fn(() => false),
  SEARCH_ACCESSIBLE_RECORDS_NOT_FOUND_STATUS: 404,
  SEARCH_NO_ACCESSIBLE_DOCUMENTS_FRAGMENT: '',
  useMutation: vi.fn(),
  withToast: vi.fn(),
}));

const { bareToolFullName, buildCatalogToolGroups, buildCatalogMcpGroups, restrictGroupsToBareNames } =
  await import('../tool-groups');
const { buildProjectChatScope } = await import('../hooks/use-project-scope-hydration');

describe('bareToolFullName', () => {
  it('strips a single instance prefix', () => {
    expect(bareToolFullName('inst-1:slack.send_message')).toBe('slack.send_message');
  });
  it('returns bare names unchanged', () => {
    expect(bareToolFullName('slack.send_message')).toBe('slack.send_message');
  });
});

describe('buildCatalogToolGroups', () => {
  it('prefixes every key with the instanceId and falls back to the index when missing', () => {
    const groups = buildCatalogToolGroups([
      { instanceId: 'i1', name: 'slack', tools: [{ fullName: 'slack.send', description: 'Send' }] },
      { name: 'jira', tools: [{ fullName: 'jira.create' }] },
      { name: 'empty', tools: [] },
    ] as never);
    expect(groups.map((g) => g.fullNames)).toEqual([['i1:slack.send'], ['local-1:jira.create']]);
    expect(groups[0]!.toolDescriptions).toEqual({ 'i1:slack.send': 'Send' });
    expect(groups[1]!.toolDescriptions).toBeUndefined();
  });
});

describe('buildCatalogMcpGroups', () => {
  it('keys tools by server _id and namespacedName', () => {
    const groups = buildCatalogMcpGroups([
      { _id: 'm1', name: 'GitHub', isAuthenticated: true, tools: [{ namespacedName: 'mcp_github_list' }] },
    ] as never);
    expect(groups).toHaveLength(1);
    expect(groups[0]).toMatchObject({ label: 'GitHub', toolsetSlug: 'mcp', instanceId: 'm1', fullNames: ['m1:mcp_github_list'] });
  });
});

describe('restrictGroupsToBareNames', () => {
  const groups = [
    {
      label: 'Slack',
      toolsetSlug: 'slack',
      instanceId: 'i1',
      fullNames: ['i1:slack.send', 'i1:slack.read'],
      toolDescriptions: { 'i1:slack.send': 'Send', 'i1:slack.read': 'Read' },
    },
    { label: 'Jira', toolsetSlug: 'jira', instanceId: 'i2', fullNames: ['i2:jira.create'] },
  ];

  it('keeps only allowed tools and drops empty groups', () => {
    const out = restrictGroupsToBareNames(groups, new Set(['slack.read']));
    expect(out).toHaveLength(1);
    expect(out[0]!.fullNames).toEqual(['i1:slack.read']);
    expect(out[0]!.toolDescriptions).toEqual({ 'i1:slack.read': 'Read' });
  });

  it('returns the same group reference when nothing was removed', () => {
    const out = restrictGroupsToBareNames(groups, new Set(['slack.send', 'slack.read', 'jira.create']));
    expect(out[0]).toBe(groups[0]);
    expect(out[1]).toBe(groups[1]);
  });

  it('matches both instances of the same tool name', () => {
    const two = [
      { label: 'A', toolsetSlug: 'slack', instanceId: 'a', fullNames: ['a:slack.send'] },
      { label: 'B', toolsetSlug: 'slack', instanceId: 'b', fullNames: ['b:slack.send'] },
    ];
    expect(restrictGroupsToBareNames(two, new Set(['slack.send']))).toHaveLength(2);
  });
});

describe('buildProjectChatScope', () => {
  const project = {
    _id: 'p1',
    orgId: 'o',
    userId: 'u',
    name: 'P',
    visibility: 'private',
    chatSharing: 'private',
    isPinned: false,
    isArchived: false,
    lastActivityAt: 0,
    createdAt: '',
    updatedAt: '',
    role: 'owner',
    members: [],
    knowledgeScope: { apps: ['app-1', 'app-2'], kb: ['kb-1'] },
    appliedFilters: {
      apps: [{ id: 'app-1', name: 'Slack', nodeType: 'app', connector: 'SLACK' }],
      kb: [{ id: 'kb-1', name: 'Specs', nodeType: 'recordGroup', connector: 'KB' }],
    },
    tools: ['slack.send', 'inst-legacy:jira.create'],
  } as never;

  const catalog = {
    toolGroups: [
      { label: 'Slack', toolsetSlug: 'slack', instanceId: 'i1', isAuthenticated: true, fullNames: ['i1:slack.send', 'i1:slack.read'] },
      { label: 'Jira', toolsetSlug: 'jira', instanceId: 'i2', isAuthenticated: true, fullNames: ['i2:jira.create'] },
      { label: 'Drive', toolsetSlug: 'drive', instanceId: 'i3', isAuthenticated: true, fullNames: ['i3:drive.list'] },
    ],
    mcpGroups: [{ label: 'GH', toolsetSlug: 'mcp', instanceId: 'm1', isAuthenticated: true, fullNames: ['m1:mcp_github_list'] }],
  };

  it('labels connectors/collections from appliedFilters and falls back to the id', () => {
    const scope = buildProjectChatScope(project, catalog);
    expect(scope.connectors).toEqual([
      { id: 'app-1', label: 'Slack', connectorKind: 'SLACK' },
      { id: 'app-2', label: 'app-2', connectorKind: '' },
    ]);
    expect(scope.knowledgeCollectionRows).toEqual([{ id: 'kb-1', name: 'Specs', sourceType: 'KB' }]);
    expect(scope.knowledgeDefaults).toEqual({ apps: ['app-1', 'app-2'], kb: ['kb-1'] });
  });

  it('restricts the catalog to project.tools, tolerating legacy prefixed entries', () => {
    const scope = buildProjectChatScope(project, catalog);
    expect(scope.toolGroups.map((g) => g.fullNames)).toEqual([['i1:slack.send'], ['i2:jira.create']]);
    expect(scope.mcpGroups).toEqual([]);
    expect(scope.toolCatalogFullNames).toEqual(['i1:slack.send', 'i2:jira.create']);
  });

  it('yields empty tool groups for a project with no tools', () => {
    const scope = buildProjectChatScope({ ...(project as object), tools: [] } as never, catalog);
    expect(scope.toolGroups).toEqual([]);
    expect(scope.toolCatalogFullNames).toEqual([]);
  });
});
