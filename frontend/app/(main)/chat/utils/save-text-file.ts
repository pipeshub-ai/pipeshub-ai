import { downloadPreviewFile } from '@/app/components/file-preview/utils';

/** Saves text as a file. A `Blob` link is a plain download, so nothing in the text is ever run. */
export function saveTextFile(name: string, text: string, mimeType: string): void {
  downloadPreviewFile({ name, blob: new Blob([text], { type: `${mimeType};charset=utf-8` }) });
}
