/**
 * Read a blob into a `data:` URL.
 *
 * Use it to show an image a user uploaded, such as an SVG org logo. A `blob:`
 * URL opened in its own tab renders on the app's origin, so script inside an
 * SVG would run there. A `data:` URL has no origin of its own.
 */
export function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}
