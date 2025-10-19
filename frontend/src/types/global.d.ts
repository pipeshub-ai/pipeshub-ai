// Global type declarations for window object extensions
declare global {
  interface Window {
    __servicesHealth?: {
      loading: boolean;
      healthy: boolean | null;
    };
    __errorContext?: {
      showError: (message: string) => void;
    };
  }
}

export {};
