import { isDocxFile, resolvePreviewMimeAfterStream, streamRecordForPreview } from '@/app/components/file-preview/utils';
import { KnowledgeBaseApi } from '@/knowledge-base/api';
import { useChatStore } from '../../../store';
import type { ChatArtifact } from '../../../types';
import { isArtifactGone } from '../artifacts-panel';

/**
 * A job's delivered file in the chat's own file preview, the way an answer's file opens: streamed by its record, the
 * record saying what kind of file it is. Rejects with a gone error (`isArtifactGone`) when the file was deleted, so
 * its card says so.
 */
export async function previewJobFile(artifact: ChatArtifact): Promise<void> {
  const recordId = artifact.recordId;
  if (!recordId) return;
  const { blob, asPdf } = await streamRecordForPreview(
    (options) => KnowledgeBaseApi.streamRecord(recordId, options),
    undefined,
    artifact.fileName,
    { suppressErrorToast: isArtifactGone },
  );
  const docx = isDocxFile(undefined, artifact.fileName);
  const previous = useChatStore.getState().previewFile;
  if (previous?.url?.startsWith('blob:')) URL.revokeObjectURL(previous.url);
  useChatStore.getState().setPreviewFile({
    id: recordId,
    url: docx ? '' : URL.createObjectURL(blob),
    blob: docx ? blob : undefined,
    name: artifact.fileName,
    type: resolvePreviewMimeAfterStream(artifact.mimeType === 'application/octet-stream' ? '' : artifact.mimeType, artifact.fileName, blob, asPdf),
    hideFileDetails: true,
    showDownload: true,
  });
}
