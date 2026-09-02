// Gemini File Search — settings types
//
// Mirrors the shape persisted by the Python backend under
// /services/geminiFileSearch in the encrypted KV store.

export interface GeminiFileSearchConfig {
  /** Master on/off switch. When false, no indexing or querying happens. */
  enabled: boolean;
  /** Generation model used at query time (e.g. "gemini-3.5-flash"). */
  generationModel: string;
  /** Embedding model used for the multimodal store index. */
  embeddingModel: string;
  /** Max distinct KB stores queried per chat turn. */
  maxStoresPerQuery: number;
  /** Max cited images downloaded + surfaced per chat turn. */
  maxMediaPerQuery: number;
}

export interface GeminiFileSearchKbStats {
  success: boolean;
  /** True when a store has been created for this KB. */
  configured: boolean;
  kbId?: string;
  storeName?: string;
  embeddingModel?: string;
  stats?: {
    sizeBytes?: number | null;
    activeDocumentsCount?: number | null;
    pendingDocumentsCount?: number | null;
    failedDocumentsCount?: number | null;
    displayName?: string | null;
  } | null;
}
