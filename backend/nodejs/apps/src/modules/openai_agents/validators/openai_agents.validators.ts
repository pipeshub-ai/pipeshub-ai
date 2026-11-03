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
  }),
});
