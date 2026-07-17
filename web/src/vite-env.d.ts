/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Per-user API key injected at build time (never hardcode real keys in a SPA). */
  readonly VITE_TAX_API_KEY?: string;
  /** Optional default tenant id for local/dev builds. */
  readonly VITE_TAX_TENANT?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
