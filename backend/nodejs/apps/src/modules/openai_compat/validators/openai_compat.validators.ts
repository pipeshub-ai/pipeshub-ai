import { z } from 'zod';

// Unknown OpenAI fields (temperature, tools, …) are dropped, not refused: clients send them by default.
const model = z.string().trim().min(1, { message: 'model is required' });

export const chatCompletionSchema = z.object({
  body: z.object({
    model,
    messages: z.array(z.unknown()).min(1, { message: 'messages is required' }),
    stream: z.boolean().optional(),
  }),
});

export const responseSchema = z.object({
  body: z.object({
    model,
    input: z.union([z.string(), z.array(z.unknown())]),
    instructions: z.string().nullish(),
    previous_response_id: z.string().nullish(),
    stream: z.boolean().optional(),
    // Read for `file_search` vector stores; other tool types are ignored.
    tools: z.array(z.unknown()).optional(),
  }),
});

const id = z.string().trim().min(1).max(200);

export const createFileSchema = z.object({
  body: z.object({ purpose: z.string().max(100).optional() }),
});

export const createVectorStoreSchema = z.object({
  body: z.object({
    name: z.string().max(255).nullish(),
    file_ids: z.array(id).max(100).optional(),
  }),
});

export const modifyVectorStoreSchema = z.object({
  params: z.object({ vectorStoreId: id }),
  body: z.object({ name: z.string().max(255).nullish() }),
});

export const createVectorStoreFileSchema = z.object({
  params: z.object({ vectorStoreId: id }),
  body: z.object({ file_id: id }),
});

export const createFileBatchSchema = z.object({
  params: z.object({ vectorStoreId: id }),
  body: z.object({ file_ids: z.array(id).min(1).max(100) }),
});

export const cancelFileBatchSchema = z.object({
  params: z.object({
    vectorStoreId: id,
    batchId: z.string().min(1).max(20000),
  }),
});
