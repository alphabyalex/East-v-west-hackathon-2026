/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_ESTIMATE_MODE?: 'api' | 'local';
  readonly VITE_API_BASE_URL?: string;
}
