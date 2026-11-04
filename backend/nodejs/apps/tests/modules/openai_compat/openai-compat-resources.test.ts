/// <reference types="mocha" />
import { expect } from 'chai';
import express from 'express';
import multer from 'multer';
import { AddressInfo } from 'net';
import { Server } from 'http';

import {
  createChatCompletion,
  createResponse,
} from '../../../src/modules/openai_compat/controller/openai_compat.controller';
import {
  createFile,
  fileContent,
  listFiles,
  retrieveFile,
} from '../../../src/modules/openai_compat/controller/files.controller';
import {
  createFileBatch,
  createVectorStore,
  createVectorStoreFile,
  listVectorStoreFiles,
  retrieveFileBatch,
  retrieveVectorStoreFile,
} from '../../../src/modules/openai_compat/controller/vector-stores.controller';
import { listProjects, projectObject } from '../../../src/modules/openai_compat/controller/projects.controller';
import {
  attachedFileIds,
  fileSearchStoreIds,
  kbIdFromVectorStoreId,
  recordIdFromFileId,
} from '../../../src/modules/openai_compat/utils/openai-format';
import {
  fileObject,
  listPage,
  listParams,
  vectorStoreObject,
} from '../../../src/modules/openai_compat/utils/openai-objects';

const file = (recordId: string, indexingStatus = 'COMPLETED') => ({
  recordId,
  filename: `${recordId}.md`,
  bytes: 10,
  createdAt: 5000,
  indexingStatus,
  kbId: 'kb',
});

describe('openai_compat resource shapes', () => {
  it('maps record and collection ids both ways and rejects ids we did not issue', () => {
    expect(recordIdFromFileId('file-abc-123')).to.equal('abc-123');
    expect(recordIdFromFileId('file-../x')).to.equal(null);
    expect(recordIdFromFileId('abc')).to.equal(null);
    expect(kbIdFromVectorStoreId('vs_kb-1')).to.equal('kb-1');
    expect(kbIdFromVectorStoreId('vs_a/b')).to.equal(null);
  });

  it('builds file and vector store objects from records', () => {
    expect(fileObject(file('r1', 'FAILED'), 'assistants')).to.include({
      id: 'file-r1',
      object: 'file',
      purpose: 'assistants',
      status: 'error',
      created_at: 5,
    });
    const store = vectorStoreObject(
      { id: 'kb', name: 'Docs', createdAt: 1000, updatedAt: 3000 },
      [file('a'), file('b', 'QUEUED'), file('c', 'FAILED')],
    );
    expect(store).to.include({ id: 'vs_kb', status: 'in_progress', usage_bytes: 30, last_active_at: 3 });
    expect(store.file_counts).to.deep.equal({ in_progress: 1, completed: 1, failed: 1, cancelled: 0, total: 3 });
  });

  it('pages lists with limit, after, before and order', () => {
    const items = ['a', 'b', 'c', 'd'].map((id) => ({ id }));
    const page = listPage(items, listParams({ limit: '2', after: 'a' }, 20, 100));
    expect(page).to.deep.include({ first_id: 'b', last_id: 'c', has_more: true });
    expect(listPage(items, listParams({ order: 'asc', limit: '1' }, 20, 100)).first_id).to.equal('d');
    expect(listPage(items, listParams({ before: 'c' }, 20, 100)).data).to.deep.equal([{ id: 'a' }, { id: 'b' }]);
    expect(listParams({ limit: '999' }, 20, 100).limit).to.equal(100);
  });

  it('reads files from message parts and stores from file_search tools', () => {
    expect(
      attachedFileIds([
        { role: 'user', content: [{ type: 'text', text: 'x' }, { type: 'file', file: { file_id: 'file-1' } }] },
        { role: 'user', content: [{ type: 'input_file', file_id: 'file-2' }, { type: 'input_file', file_data: 'b64' }] },
        { role: 'user', content: 'plain' },
      ]),
    ).to.deep.equal(['file-1', 'file-2']);
    expect(
      fileSearchStoreIds([{ type: 'web_search' }, { type: 'file_search', vector_store_ids: ['vs_a', 'vs_b'] }]),
    ).to.deep.equal(['vs_a', 'vs_b']);
  });

  it('maps a project to an organization.project', () => {
    expect(
      projectObject({ _id: 'p1', name: 'Launch', createdAt: '1970-01-01T00:00:10.000Z', isArchived: true, updatedAt: 20000 }),
    ).to.deep.equal({ id: 'p1', object: 'organization.project', name: 'Launch', created_at: 10, archived_at: 20, status: 'archived' });
  });
});

describe('openai_compat files, vector stores and projects against a fake collection API', () => {
  const CONV = '0123456789abcdef01234567';
  const PROJECT = 'aaaaaaaaaaaaaaaaaaaaaaaa';
  let upstream: Server;
  let api: Server;
  let base = '';
  const kbs = new Map<string, { id: string; name: string; createdAtTimestamp: number; owner: boolean }>();
  const records = new Map<string, { id: string; kbId: string; name: string; ext: string; bytes: Buffer; mime: string; status: string }>();
  const chatBodies: any[] = [];
  let seq = 0;

  before(async () => {
    const fake = express();
    fake.use(express.json());
    const up = multer({ storage: multer.memoryStorage() });
    fake.get('/api/v1/knowledgeBase', (req, res) => {
      const search = typeof req.query.search === 'string' ? req.query.search : '';
      const owned = req.query.permissions === 'OWNER';
      res.json({
        knowledgeBases: [...kbs.values()]
          .filter((kb) => (search === '' || kb.name.includes(search)) && (!owned || kb.owner))
          .map((kb) => ({ id: kb.id, name: kb.name, createdAtTimestamp: kb.createdAtTimestamp })),
        pagination: { hasNext: false },
      });
    });
    fake.post('/api/v1/knowledgeBase', (req, res) => {
      const id = `kb${++seq}`;
      kbs.set(id, { id, name: req.body.kbName, createdAtTimestamp: 1000 + seq, owner: true });
      res.json({ id, name: req.body.kbName, createdAtTimestamp: 1000 + seq });
    });
    fake.get('/api/v1/knowledgeBase/knowledge-hub/nodes/app/:kbId', (req, res) => {
      res.json({
        items: [...records.values()]
          .filter((r) => r.kbId === req.params.kbId)
          .map((r) => ({ id: r.id, name: r.name, extension: r.ext, nodeType: 'record', recordType: 'FILE', sizeInBytes: r.bytes.length, createdAt: 2000, indexingStatus: r.status })),
        pagination: { hasNext: false },
      });
    });
    fake.get('/api/v1/knowledgeBase/record/:id', (req, res) => {
      const r = records.get(req.params.id);
      if (!r) return void res.status(404).json({ error: { message: 'Record not found' } });
      res.json({ record: { recordName: r.name, sizeInBytes: r.bytes.length, createdAtTimestamp: 2000, indexingStatus: r.status, mimeType: r.mime }, knowledgeBase: { id: r.kbId } });
    });
    fake.get('/api/v1/knowledgeBase/stream/record/:id', (req, res) => {
      const r = records.get(req.params.id)!;
      res.setHeader('content-type', r.mime);
      res.end(r.bytes);
    });
    fake.post('/api/v1/knowledgeBase/:kbId/upload', up.array('files'), (req, res) => {
      const f = (req.files as Express.Multer.File[])[0]!;
      const id = `rec${++seq}`;
      const dot = f.originalname.lastIndexOf('.');
      records.set(id, { id, kbId: req.params.kbId!, name: f.originalname.slice(0, dot), ext: f.originalname.slice(dot + 1), bytes: f.buffer, mime: f.mimetype, status: 'COMPLETED' });
      res.writeHead(200, { 'Content-Type': 'text/event-stream' });
      res.end(`event: file:succeeded\ndata: ${JSON.stringify({ recordId: id })}\n\nevent: done\ndata: {}\n\n`);
    });
    fake.get('/api/v1/projects', (_req, res) => {
      res.json({ projects: [{ _id: PROJECT, name: 'Launch', createdAt: 1000 }], pagination: { hasNext: false } });
    });
    fake.post(/\/api\/v1\/conversations(\/.*)?\/stream$/, (req, res) => {
      chatBodies.push(req.body);
      res.writeHead(200, { 'Content-Type': 'text/event-stream' });
      res.write(`event: CUSTOM\ndata: ${JSON.stringify({ type: 'CUSTOM', name: 'conversation_created', value: { conversationId: CONV } })}\n\n`);
      res.end(`event: RUN_FINISHED\ndata: ${JSON.stringify({ type: 'RUN_FINISHED', result: { conversation: { _id: CONV, messages: [{ messageType: 'bot_response', content: 'ok', citations: [] }] } } })}\n\n`);
    });
    upstream = await new Promise<Server>((r) => {
      const s = fake.listen(0, '127.0.0.1', () => r(s));
    });
    const deps = {
      appConfig: { esBackend: `http://127.0.0.1:${(upstream.address() as AddressInfo).port}`, frontendUrl: 'https://app.test' } as any,
      readModels: async () => ({ llm: [{ modelKey: 'k', provider: 'openAI', isDefault: true, configuration: { model: 'gpt-5' } }] }),
    };
    const app = express();
    app.use(express.json());
    const u = multer({ storage: multer.memoryStorage() });
    app.post('/files', u.single('file'), createFile(deps));
    app.get('/files', listFiles(deps));
    app.get('/files/:fileId', retrieveFile(deps));
    app.get('/files/:fileId/content', fileContent(deps));
    app.post('/vector_stores', createVectorStore(deps));
    app.post('/vector_stores/:vectorStoreId/files', createVectorStoreFile(deps));
    app.get('/vector_stores/:vectorStoreId/files', listVectorStoreFiles(deps));
    app.get('/vector_stores/:vectorStoreId/files/:fileId', retrieveVectorStoreFile(deps));
    app.post('/vector_stores/:vectorStoreId/file_batches', createFileBatch(deps));
    app.get('/vector_stores/:vectorStoreId/file_batches/:batchId', retrieveFileBatch(deps));
    app.get('/organization/projects', listProjects(deps));
    app.post('/chat/completions', createChatCompletion(deps));
    app.post('/responses', createResponse(deps));
    api = await new Promise<Server>((r) => {
      const s = app.listen(0, '127.0.0.1', () => r(s));
    });
    base = `http://127.0.0.1:${(api.address() as AddressInfo).port}`;
  });

  after(() => {
    api.close();
    upstream.close();
  });

  const json = (method: string, path: string, body?: unknown, headers: Record<string, string> = {}) =>
    fetch(`${base}${path}`, {
      method,
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer t', ...headers },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  const upload = async (name: string, text: string) => {
    const form = new FormData();
    form.append('purpose', 'assistants');
    form.append('file', new Blob([text], { type: 'text/markdown' }), name);
    return (await fetch(`${base}/files`, { method: 'POST', headers: { Authorization: 'Bearer t' }, body: form })).json();
  };

  it('uploads into one owned files collection, created once, and lists, reads and downloads the file', async () => {
    kbs.set('shared', { id: 'shared', name: 'OpenAI API files', createdAtTimestamp: 1, owner: false });
    const a = await upload('a.md', 'alpha');
    const b = await upload('b.md', 'beta');
    expect(a).to.include({ object: 'file', filename: 'a.md', purpose: 'assistants', bytes: 5 });
    const filesKbs = [...kbs.values()].filter((k) => k.name === 'OpenAI API files' && k.owner);
    expect(filesKbs).to.have.length(1);
    expect(records.get(recordIdFromFileId(b.id)!)!.kbId).to.equal(filesKbs[0]!.id);
    const list = await (await json('GET', '/files')).json();
    expect(list.data.map((f: any) => f.id).sort()).to.deep.equal([a.id, b.id].sort());
    expect((await (await json('GET', `/files/${a.id}`)).json()).filename).to.equal('a.md');
    expect(await (await json('GET', `/files/${a.id}/content`)).text()).to.equal('alpha');
    expect((await json('GET', '/files/not-ours')).status).to.equal(404);
  });

  it('copies a file into a new vector store and finds it again by its original id', async () => {
    const f = await upload('c.md', 'gamma');
    const vs = await (await json('POST', '/vector_stores', { name: 'Store', file_ids: [f.id] })).json();
    expect(vs).to.include({ object: 'vector_store', name: 'Store' });
    expect(vs.file_counts.total).to.equal(1);
    const kbId = kbIdFromVectorStoreId(vs.id)!;
    const copies = [...records.values()].filter((r) => r.kbId === kbId);
    expect(copies).to.have.length(1);
    expect(copies[0]!.bytes.toString()).to.equal('gamma');
    const got = await (await json('GET', `/vector_stores/${vs.id}/files/${f.id}`)).json();
    expect(got).to.include({ id: f.id, object: 'vector_store.file', status: 'completed', vector_store_id: vs.id });
    // Adding it again does not copy it twice.
    await json('POST', `/vector_stores/${vs.id}/files`, { file_id: f.id });
    expect([...records.values()].filter((r) => r.kbId === kbId)).to.have.length(1);
    const batch = await (await json('POST', `/vector_stores/${vs.id}/file_batches`, { file_ids: [f.id] })).json();
    expect(batch).to.include({ object: 'vector_store.files_batch', status: 'completed' });
    const again = await (await json('GET', `/vector_stores/${vs.id}/file_batches/${batch.id}`)).json();
    expect(again.file_counts).to.include({ completed: 1, total: 1 });
    expect((await json('POST', `/vector_stores/${vs.id}/files`, { file_id: 'nope' })).status).to.equal(400);
  });

  it('lists projects as organization.project objects', async () => {
    const list = await (await json('GET', '/organization/projects')).json();
    expect(list.data).to.deep.equal([{ id: PROJECT, object: 'organization.project', name: 'Launch', created_at: 1, archived_at: null, status: 'active' }]);
  });

  it('runs a chat in the OpenAI-Project project with file parts as attachments', async () => {
    chatBodies.length = 0;
    const res = await json('POST', '/chat/completions', {
      model: 'gpt-5',
      messages: [{ role: 'user', content: [{ type: 'text', text: 'read it' }, { type: 'file', file: { file_id: 'file-r1' } }] }],
    }, { 'OpenAI-Project': PROJECT });
    expect(res.status).to.equal(200);
    expect(chatBodies[0]).to.deep.include({ projectId: PROJECT, attachments: [{ recordId: 'r1' }], query: 'read it' });
  });

  it('scopes a Responses chat to its file_search vector stores and keeps a follow-up out of the header project', async () => {
    chatBodies.length = 0;
    const first = await (await json('POST', '/responses', {
      model: 'gpt-5',
      input: [{ role: 'user', content: [{ type: 'input_file', file_id: 'file-r2' }] }],
      tools: [{ type: 'file_search', vector_store_ids: ['vs_kb1', 'vs_kb1'] }],
    })).json();
    expect(chatBodies[0]).to.deep.include({ filters: { apps: ['kb1'] }, attachments: [{ recordId: 'r2' }], query: 'Use the attached files.' });
    await json('POST', '/responses', { model: 'gpt-5', input: 'more', previous_response_id: first.id }, { 'OpenAI-Project': PROJECT });
    expect(chatBodies[1]).to.not.have.property('projectId');
  });

  it('refuses a malformed project header, file id or vector store id', async () => {
    const msg = [{ role: 'user', content: 'hi' }];
    const bad = async (body: any, headers = {}) => (await json('POST', body.messages ? '/chat/completions' : '/responses', body, headers)).json();
    expect((await bad({ model: 'gpt-5', messages: msg }, { 'OpenAI-Project': 'x' })).error.param).to.equal('OpenAI-Project');
    expect((await bad({ model: 'gpt-5', messages: [{ role: 'user', content: [{ type: 'file', file: { file_id: 'x' } }] }] })).error.param).to.equal('messages');
    expect((await bad({ model: 'gpt-5', input: 'hi', tools: [{ type: 'file_search', vector_store_ids: ['nope'] }] })).error.param).to.equal('tools');
  });
});
