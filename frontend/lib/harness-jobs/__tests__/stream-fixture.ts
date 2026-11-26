/**
 * The chat suites' own durable streams (`app/(main)/chat/__tests__/durable-frames.ts`, the shape
 * `GET …/runs/:runId/stream` sends, an `SSEEvent` with an `id:` each), so the adapter is fed exactly what
 * `followDurableRun` hands its callbacks.
 */
export { completedRun, cancelledRun, runError } from '@/chat/__tests__/durable-frames';
