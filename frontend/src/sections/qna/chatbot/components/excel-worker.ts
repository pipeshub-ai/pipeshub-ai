// Web Worker for Excel processing - offloads heavy computation from main thread
import * as XLSX from 'xlsx';

interface ProcessMessage {
  type: 'PROCESS_EXCEL';
  buffer: ArrayBuffer;
  options: any;
}

interface ProgressMessage {
  type: 'PROGRESS';
  message: string;
  progress: number;
}

interface ResultMessage {
  type: 'RESULT';
  workbookData: any;
  availableSheets: string[];
  rowMapping: [number, number][];
}

interface ErrorMessage {
  type: 'ERROR';
  error: string;
}

type WorkerMessage = ProcessMessage;
type WorkerResponse = ProgressMessage | ResultMessage | ErrorMessage;

globalThis.onmessage = async (e: MessageEvent<WorkerMessage>) => {
  const { type, buffer, options } = e.data;

  if (type !== 'PROCESS_EXCEL') return;

  try {
    postProgress('Reading Excel file...', 10);
    
    const workbook = XLSX.read(buffer, options);
    const totalSheets = workbook.SheetNames.length;
    
    postProgress(`Processing ${totalSheets} sheet(s)...`, 20);

    const processedWorkbook: any = {};
    const rowMapping = new Map<number, number>();

    for (let sheetIndex = 0; sheetIndex < totalSheets; sheetIndex += 1) {
      const sheetName = workbook.SheetNames[sheetIndex];
      const progress = 20 + ((sheetIndex / totalSheets) * 70);
      
      postProgress(`Processing sheet ${sheetIndex + 1}/${totalSheets}...`, progress);

      const worksheet = workbook.Sheets[sheetName];
      if (!worksheet['!ref']) {
        // eslint-disable-next-line no-continue
        continue;
      }

      const range = XLSX.utils.decode_range(worksheet['!ref']);
      const headerRowIndex = range.s.r;

      // Track hidden rows
      const hiddenRows = new Set<number>();
      if (worksheet['!rows']) {
        worksheet['!rows'].forEach((row: any, index: number) => {
          if (row?.hidden) hiddenRows.add(index + range.s.r);
        });
      }

      const actualCols = range.e.c + 1;
      const maxAllowedCols = 1000;
      const totalCols = Math.min(Math.max(actualCols, 13), maxAllowedCols);
      const visibleColumns = Array.from({ length: totalCols }, (_, i) => i);

      // Process headers - optimized with direct access
      const headers = new Array(visibleColumns.length);
      for (let i = 0; i < visibleColumns.length; i += 1) {
        const colIndex = visibleColumns[i];
        const cellAddress = XLSX.utils.encode_cell({ r: headerRowIndex, c: colIndex });
        const cell = worksheet[cellAddress];
        headers[i] = getCellValue(cell) || `Column ${String.fromCharCode(65 + colIndex)}`;
      }

      // Process rows - optimized batch processing
      const data: any[] = [];
      const MAX_ROWS = 100000;
      const maxRows = Math.min(range.e.r + 2, headerRowIndex + MAX_ROWS);
      let displayIndex = 0;

      // Pre-allocate array for better performance
      const estimatedRows = maxRows - headerRowIndex;
      data.length = 0;

      for (let rowIndex = headerRowIndex; rowIndex < maxRows; rowIndex += 1) {
        if (!hiddenRows.has(rowIndex)) {
          const excelRowNum = rowIndex + 1;
          const rowData: any = {
            __rowNum: excelRowNum,
            __isHeaderRow: rowIndex === headerRowIndex,
            __sheetName: sheetName,
          };

          // Optimized cell processing
          for (let i = 0; i < visibleColumns.length; i += 1) {
            const colIndex = visibleColumns[i];
            const cellAddress = XLSX.utils.encode_cell({ r: rowIndex, c: colIndex });
            const cell = worksheet[cellAddress];
            rowData[headers[i]] = getCellValue(cell);
          }

          rowMapping.set(excelRowNum, displayIndex);
          displayIndex += 1;
          data.push(rowData);
        }
      }

      processedWorkbook[sheetName] = {
        headers,
        data,
        headerRowIndex: headerRowIndex + 1,
        totalColumns: visibleColumns.length,
        hiddenColumns: [],
        visibleColumns,
        columnMapping: Object.fromEntries(visibleColumns.map((col, idx) => [idx, col])),
        originalTotalColumns: totalCols,
      };
    }

    postProgress('Finalizing...', 95);

    // Convert Map to array for transfer
    const rowMappingArray: [number, number][] = Array.from(rowMapping.entries());

    const result: ResultMessage = {
      type: 'RESULT',
      workbookData: processedWorkbook,
      availableSheets: Object.keys(processedWorkbook),
      rowMapping: rowMappingArray,
    };

    postProgress('Complete!', 100);
    globalThis.postMessage(result);

  } catch (error: any) {
    const errorMsg: ErrorMessage = {
      type: 'ERROR',
      error: error.message || 'Unknown error processing Excel file',
    };
    globalThis.postMessage(errorMsg);
  }
};

function postProgress(message: string, progress: number) {
  const msg: ProgressMessage = {
    type: 'PROGRESS',
    message,
    progress,
  };
  globalThis.postMessage(msg);
}

function getCellValue(cell: any): any {
  if (!cell) return '';

  try {
    // Handle rich text
    if (cell.r && Array.isArray(cell.r)) {
      return cell.r.map((fragment: any) => fragment.t).join('');
    }

    // Handle formatted value
    if (cell.w) return String(cell.w).trim();

    // Handle raw value
    if (cell.v !== undefined && cell.v !== null) {
      if (cell.v instanceof Date) {
        return cell.v.toLocaleDateString();
      }
      return String(cell.v).trim();
    }
  } catch (err) {
    console.warn('Error processing cell:', err);
  }

  return '';
}

export {};