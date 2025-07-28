import axios from 'src/utils/axios';

import { CONFIG } from 'src/config-global';

import type { RecordDetailsResponse } from './types/record-details';
import type { KnowledgeBaseResponse } from './types/knowledge-base';
import type { SearchFilters, SearchResponse } from './types/search-response';

// export const fetchKnowledgeBaseDetails = async () => {
//   try {
//     const response = await axios.get(`${API_BASE_URL}/api/v1/knowledgebase`);
//     return response.data;
//   } catch (error) {
//     console.error('Error fetching knowledge base details:', error);
//     throw error;
//   }
// };

export const fetchKnowledgeBaseDetails = async (
  queryParams: URLSearchParams
): Promise<KnowledgeBaseResponse> => {
  try {
    const response = await axios.get<KnowledgeBaseResponse>(
      `${CONFIG.backendUrl}/api/v1/knowledgebase`,
      { params: queryParams }
    );
    return response.data;
  } catch (error) {
    throw new Error('Error fetching knowledge base details');
  }
};

export const searchKnowledgeBase = async (
  searchtext: string,
  topK: number = 10,
  filters: SearchFilters = {}
): Promise<SearchResponse['searchResponse']> => {
  try {
    const queryParams = new URLSearchParams({
      topK: topK.toString(),
      ...(filters as any),
    });
    const body = {
      query: searchtext,
      limit: topK,
      filters: {
        departments: filters.department || [],
        moduleIds: filters.moduleId || [],
        appSpecificRecordTypes: filters.appSpecificRecordType || [],
        apps:filters.app || [],
        kb:filters.kb || []
      }
    };

    const response = await axios.post<SearchResponse>(`/api/v1/search`, body);
    return response.data.searchResponse;
  } catch (error) {
    throw new Error('Erro searching knowledge base ');
  }
};

export const uploadKnowledgeBaseFiles = async (formData: FormData) => {
  try {
    const response = await axios.post(`${CONFIG.backendUrl}/api/v1/knowledgebase`, formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    });
    return response.data;
  } catch (error) {
    throw new Error('Error uploading files');
  }
};

// export const fetchDepartments = async () => {
//   try {
//     const response = await axios.get<Departments[]>(`${CONFIG.backendUrl}/api/v1/departments/`);
//     return response.data;
//   } catch (error) {
//     throw new Error('Error fetching departments');
//   }
// };

// export const fetchTags = async () => {
//   try {
//     const response = await axios.get<SearchTagsRecords[]>(
//       `${CONFIG.backendUrl}/api/v1/search-tags?category=Records`
//     );
//     return response.data;
//   } catch (error) {
//     throw new Error('Error fetching Tags');
//   }
// };

// export const fetchModules = async () => {
//   try {
//     const response = await axios.get<Modules[]>(`${CONFIG.backendUrl}/api/v1/modules`);
//     return response.data;
//   } catch (error) {
//     throw new Error('Error fetching modules');
//   }
// };

// export const fetchRecordCategories = async () => {
//   try {
//     const response = await axios.get<RecordCategories[]>(
//       `${CONFIG.backendUrl}/api/v1/recordCategories`
//     );
//     return response.data;
//   } catch (error) {
//     throw new Error('Error fetching record categories');
//   }
// };

export const fetchRecordDetails = async (recordId: string): Promise<RecordDetailsResponse> => {
  try {
    const response = await axios.get<RecordDetailsResponse>(
      `${CONFIG.backendUrl}/api/v1/knowledgebase/record/${recordId}`
    );
    return response.data;
  } catch (error) {
    throw new Error('Error fetching record details');
  }
};

export const handleDownloadDocument = async (
  externalRecordId: string,
  fileName: string
): Promise<void> => {
  try {
    const response = await axios.get(
      `${CONFIG.backendUrl}/api/v1/document/${externalRecordId}/download`,
      { responseType: 'blob' } // Set response type to blob to handle binary data
    );
    // Read the blob response as text to check if it's JSON with signedUrl
    const reader = new FileReader();
    const textPromise = new Promise<string>((resolve) => {
      reader.onload = () => {
        resolve(reader.result?.toString() || '');
      };
    });

    reader.readAsText(response.data);
    const text = await textPromise;

    let downloadUrl;
    // Use the provided fileName instead of extracting it from headers or URL
    // Get filename from Content-Disposition header if available
    let filename;
    const contentDisposition = response.headers['content-disposition'];
    if (contentDisposition) {
      const filenameMatch = contentDisposition.match(/filename="?([^"]*)"?/);
      if (filenameMatch && filenameMatch[1]) {
        filename = filenameMatch[1];
      }
    }

    if (!filename) {
      filename = fileName || `document-${externalRecordId}`;
    }

    // Try to parse as JSON to check for signedUrl property
    try {
      const jsonData = JSON.parse(text);
      if (jsonData && jsonData.signedUrl) {
        // Create a hidden link with download attribute
        const downloadLink = document.createElement('a');
        downloadLink.href = jsonData.signedUrl;
        downloadLink.setAttribute('download', filename); // Use provided filename
        downloadLink.setAttribute('target', '_blank');
        downloadLink.style.display = 'none';

        document.body.appendChild(downloadLink);
        downloadLink.click();
        document.body.removeChild(downloadLink);
      }
    } catch (e) {
      // Case 2: Response is binary data
      const contentType = response.headers['content-type'] || 'application/octet-stream';
      const blob = new Blob([response.data], { type: contentType });
      downloadUrl = URL.createObjectURL(blob);

      // Create a temporary anchor element for download of binary data
      const link = document.createElement('a');
      link.href = downloadUrl;
      link.setAttribute('download', filename); // Use provided filename

      // Append to the document, trigger click, and then remove
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);

      // Clean up the blob URL we created
      URL.revokeObjectURL(downloadUrl);
    }
  } catch (error) {
    throw new Error('Failed to download document');
  }
};
