import multer from 'multer';

// multer decodes the multipart `filename` as Latin-1 unless told otherwise,
// turning "résumé.pdf" into "rÃ©sumÃ©.pdf". Browsers send it as raw UTF-8.
// @types/multer does not declare `defParamCharset`, hence the widened type.
export function createMulter(options: multer.Options = {}): multer.Multer {
  const withUtf8Names: multer.Options & { defParamCharset: string } = {
    ...options,
    defParamCharset: 'utf8',
  };
  return multer(withUtf8Names);
}
