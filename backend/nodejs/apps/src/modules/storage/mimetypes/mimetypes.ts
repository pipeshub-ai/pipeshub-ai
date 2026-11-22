import { UPLOAD_EXTENSION_TO_MIME } from './file-types.generated';

// Keys are lower case: getMimeType and the upload allowlists lower-case the extension first.
export const extensionToMimeType: Record<string, string> = {
  ...UPLOAD_EXTENSION_TO_MIME,
};

// Helper function to get mimetype
export const getMimeType = (extension: string): string => {
  if (!extension) {
    return '';
  }
  const normalizedExtension = extension.toLowerCase().replace('.', '');
  return extensionToMimeType[normalizedExtension] || '';
};
