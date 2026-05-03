# File Preview Feature

## 1. File Data & File Fetching

### Why Blob URLs?

Files are fetched as blobs and converted to object URLs because:
- **Security**: Backend returns files as binary streams, not public URLs
- **Authentication**: Each request includes auth token via axios interceptor
- **Control**: Blob URLs can be revoked to free memory when preview closes
- **Reliability**: No CORS issues, no external dependencies

### Fetch Flow

1. User double-clicks file in Knowledge Base table
2. `handlePreviewFile` calls `GET /api/v1/knowledgeBase/stream/record/:recordId`
3. API returns file as blob (`responseType: 'blob'`)
4. Create object URL: `URL.createObjectURL(blob)`
5. Store blob URL in state
6. Pass URL to renderer component
7. Cleanup: `URL.revokeObjectURL(url)` on unmount

### API Method

```typescript
// app/(main)/knowledge-base/api.ts
export const KnowledgeBaseApi = {
  async getRecordStream(recordId: string): Promise<Blob> {
    const { data } = await apiClient.get(
      `/api/v1/knowledgeBase/stream/record/${recordId}`,
      { responseType: 'blob' }
    );
    return data;
  },
};
```

### Usage in Page

```typescript
// app/(main)/knowledge-base/page.tsx
const handlePreviewFile = useCallback(async (item: KnowledgeHubNode) => {
  if (item.nodeType !== 'record') return;
  
  try {
    setPreviewFile({
      id: item.id,
      name: item.name,
      url: '',
      type: item.mimeType || item.extension || '',
      size: item.sizeInBytes,
      isLoading: true,
    });
    
    const blob = await KnowledgeBaseApi.getRecordStream(item.id);
    const url = URL.createObjectURL(blob);
    
    setPreviewFile(prev => prev ? { ...prev, url, isLoading: false } : null);
  } catch (error) {
    console.error('Failed to load file preview:', error);
    setPreviewFile(prev => prev ? { ...prev, error: 'Failed to load', isLoading: false } : null);
  }
}, []);
```

### Memory Cleanup

```typescript
// In preview component
useEffect(() => {
  return () => {
    if (file?.url?.startsWith('blob:')) {
      URL.revokeObjectURL(file.url);
    }
  };
}, [file?.url]);
```

## 2. File Type Switching

### Renderer Router

The `file-preview-renderer.tsx` component routes to specialized renderers based on MIME type or file extension:

```typescript
function getRendererType(mimeType: string, extension: string): RendererType {
  // Exact MIME type matches
  if (mimeType === 'application/pdf') return 'pdf';
  if (mimeType === 'text/markdown' || extension === '.md') return 'markdown';
  if (mimeType === 'application/vnd.openxmlformats-officedocument.wordprocessingml.document') return 'word';
  if (mimeType === 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet') return 'excel';
  
  // MIME type prefixes
  if (mimeType.startsWith('image/')) return 'image';
  if (mimeType.startsWith('video/')) return 'video';
  if (mimeType.startsWith('audio/')) return 'audio';
  if (mimeType.startsWith('text/')) return 'text';
  
  // Extension-based fallback for code files
  const codeExtensions = ['.js', '.ts', '.tsx', '.jsx', '.py', '.java', '.c', '.cpp', '.go', '.rs', '.php', '.rb'];
  if (codeExtensions.includes(extension)) return 'code';
  
  return 'document'; // Fallback renderer
}

export function FilePreviewRenderer({ file }: { file: FilePreviewFile }) {
  const rendererType = getRendererType(file.type, getExtension(file.name));
  
  switch (rendererType) {
    case 'pdf': return <PDFRenderer fileUrl={file.url} />;
    case 'image': return <ImageRenderer fileUrl={file.url} fileName={file.name} />;
    case 'video': return <VideoRenderer fileUrl={file.url} />;
    case 'audio': return <AudioRenderer fileUrl={file.url} />;
    case 'markdown': return <MarkdownRenderer fileUrl={file.url} />;
    case 'code': return <CodeRenderer fileUrl={file.url} extension={getExtension(file.name)} />;
    case 'text': return <TextRenderer fileUrl={file.url} />;
    case 'word': return <WordRenderer fileUrl={file.url} />;
    case 'excel': return <ExcelRenderer fileUrl={file.url} />;
    default: return <DocumentRenderer file={file} />;
  }
}
```

### Specialized Renderers

| Renderer | File Types | Library | Purpose |
|----------|-----------|---------|---------|
| `PDFRenderer` | `.pdf` | `react-pdf-highlighter` | Professional PDF viewer with bounding-box citation highlights |
| `ImageRenderer` | `.jpg`, `.png`, `.gif`, `.svg` | Native `<img>` | Browser-native image display |
| `CodeRenderer` | `.js`, `.ts`, `.py`, `.java` | `react-syntax-highlighter` | Syntax highlighting for code |
| `TextRenderer` | `.txt`, `.log` | `react-syntax-highlighter` | Plain text with line numbers |
| `MarkdownRenderer` | `.md` | `react-markdown` + `rehype-raw` + `rehype-sanitize` | GitHub-flavored markdown |
| `VideoRenderer` | `.mp4`, `.webm` | `react-player` | Video playback with controls |
| `AudioRenderer` | `.mp3`, `.wav` | `react-player` | Audio playback |
| `DocxRenderer` | `.docx` | `docx-preview` | High-fidelity DOCX rendering (page breaks, headers, footers) |
| `HtmlRenderer` | `.html`, `.htm` | `dompurify` | Sanitized HTML rendering in-page |
| `SpreadsheetRenderer` | `.xlsx`, `.csv` | `xlsx` (SheetJS) | Native HTML table with sheet tabs |
| `DocumentRenderer` | Fallback | Custom | Download button for unsupported types |

### Why Specialized Renderers?

**Problems with iframe:**
- Limited control over UI/UX
- CORS/X-Frame-Options security blocks
- No syntax highlighting for code
- Poor PDF features (no zoom/search)
- Poor mobile support
- No Office document support

**Solution:** Use purpose-built libraries that:
- Run in-browser (no iframe restrictions)
- Provide rich features (zoom, search, syntax highlighting)
- Support mobile/responsive layouts
- Handle complex formats (Office docs, PDFs)
