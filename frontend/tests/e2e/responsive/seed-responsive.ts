import type { APIRequestContext } from '@playwright/test';
import { createTestKb } from '../knowledge-base/kb-upload.helpers';

export interface SeedIds {
  kbId: string;
  recordId: string;
  agentId?: string;
  projectId?: string;
  conversationId?: string;
  /** A collection with nothing in it. */
  emptyKbId: string;
  mcpServerId?: string;
}

export const SEED_KB_NAME = 'Responsive audit collection with a deliberately long name 0123456789';
// 60 characters, so long file names show up in every list and preview.
export const SEED_FILE_NAME = 'responsive-audit-record-with-a-deliberately-long-name-01.txt';
export const SEED_SECOND_FILE_NAME = 'responsive-audit-second.txt';
export const SEED_AGENT_NAME = 'Responsive audit agent';
export const SEED_PROJECT_NAME = 'Responsive audit project';
export const SEED_EMPTY_KB_NAME = 'Responsive audit empty collection';
export const SEED_MCP_SERVER_NAME = 'Responsive audit MCP server';

type Project = { _id?: string; name?: string };
type Named = { id?: string; _key?: string; name?: string };

/** Same call and answer as `uploadFileByApi`, through the context the caller signed in. */
async function upload(api: APIRequestContext, kbId: string, name: string, text: string): Promise<string> {
  const response = await api.post(`/api/v1/knowledgeBase/${kbId}/upload`, {
    multipart: { files: { name, mimeType: 'text/plain', buffer: Buffer.from(text) } },
  });
  const body = await response.text();
  const succeeded = body.match(/event: file:succeeded\ndata: (.*)\n/);
  if (!response.ok() || !succeeded) throw new Error(`uploading ${name} failed [${response.status()}]: ${body.slice(0, 500)}`);
  return (JSON.parse(succeeded[1]) as { recordId: string }).recordId;
}

async function getJson<T>(api: APIRequestContext, url: string): Promise<T> {
  const response = await api.get(url);
  if (!response.ok()) throw new Error(`GET ${url} failed [${response.status()}]: ${await response.text()}`);
  return (await response.json()) as T;
}

/**
 * The collection, record and agent the audit pages show. Reuses what an
 * earlier run created (matched by name), so repeated runs do not pile up data.
 */
export async function seedResponsiveData(api: APIRequestContext): Promise<SeedIds> {
  const kbs = await getJson<{ knowledgeBases?: Named[] }>(
    api,
    `/api/v1/knowledgeBase?limit=100&search=${encodeURIComponent(SEED_KB_NAME)}`,
  );
  const existingKb = kbs.knowledgeBases?.find((kb) => kb.name === SEED_KB_NAME);
  const kbId = existingKb?.id ?? (await createTestKb(api, SEED_KB_NAME)).id;
  const emptyKbs = await getJson<{ knowledgeBases?: Named[] }>(
    api,
    `/api/v1/knowledgeBase?limit=100&search=${encodeURIComponent(SEED_EMPTY_KB_NAME)}`,
  );
  const emptyKbId =
    emptyKbs.knowledgeBases?.find((kb) => kb.name === SEED_EMPTY_KB_NAME)?.id ?? (await createTestKb(api, SEED_EMPTY_KB_NAME)).id;

  // The knowledge hub lists a collection as an `app` node, and its records by
  // name without the extension.
  const items =
    (await getJson<{ items?: Named[] }>(api, `/api/v1/knowledgeBase/knowledge-hub/nodes/app/${kbId}?limit=100`)).items ?? [];
  const has = (file: string) => items.find((r) => r.name === file.replace(/\.txt$/, ''));
  const existingRecord = has(SEED_FILE_NAME);
  const recordId =
    existingRecord?.id ??
    (await upload(api, kbId, SEED_FILE_NAME, 'Responsive audit record.\nIt only needs to exist and open in the preview.\n'));

  // A second row, so a scenario can select two.
  if (!has(SEED_SECOND_FILE_NAME)) {
    await upload(api, kbId, SEED_SECOND_FILE_NAME, 'Second row.\n');
  }

  // An agent needs no AI model to be created; if the API ever refuses one,
  // the agent routes skip instead of failing.
  const agents = await getJson<{ agents?: Named[] }>(api, '/api/v1/agents?limit=100');
  let agentId = agents.agents?.find((a) => a.name === SEED_AGENT_NAME)?._key;
  if (!agentId) {
    const response = await api.post('/api/v1/agents/create', {
      data: {
        name: SEED_AGENT_NAME,
        description: 'Used by the viewport audit',
        systemPrompt: 'You are a helpful assistant.',
        models: [],
        toolsets: [],
        knowledge: [],
      },
    });
    if (response.ok()) agentId = ((await response.json()) as { agent?: Named }).agent?._key;
  }

  // Like the agent, a project the API refuses leaves projectId unset and the project route skips.
  const projectRows = await api.get(`/api/v1/projects?limit=100&search=${encodeURIComponent(SEED_PROJECT_NAME)}`);
  const projects = projectRows.ok()
    ? ((await projectRows.json()) as { projects?: Project[] }).projects
    : undefined;
  let projectId = projects?.find((p) => p.name === SEED_PROJECT_NAME)?._id;
  if (!projectId) {
    const response = await api.post('/api/v1/projects', { data: { name: SEED_PROJECT_NAME } });
    if (response.ok()) projectId = ((await response.json()) as { project?: Project }).project?._id;
  }

  // A custom server with no sign-in, so the MCP list has a row to manage; unset if the API refuses it.
  const servers = await api.get('/api/v1/mcp-servers/instances');
  const serverRows = servers.ok() ? ((await servers.json()) as { instances?: { _id?: string; name?: string }[] }).instances : undefined;
  let mcpServerId = serverRows?.find((s) => s.name === SEED_MCP_SERVER_NAME)?._id;
  if (!mcpServerId) {
    const response = await api.post('/api/v1/mcp-servers/instances', {
      data: {
        name: SEED_MCP_SERVER_NAME,
        typeId: null,
        scope: 'org',
        transport: 'streamable_http',
        authMode: 'none',
        useAdminAuth: false,
        description: 'Used by the viewport audit',
        url: 'https://example.com/mcp',
      },
    });
    if (response.ok()) mcpServerId = ((await response.json()) as { _id?: string })._id;
  }

  return { kbId, recordId, agentId, projectId, emptyKbId, mcpServerId };
}
