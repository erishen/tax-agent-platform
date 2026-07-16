const API_BASE = '/api';
const TENANT_STORAGE_KEY = 'tax-agent-tenant';
const API_KEY_STORAGE_KEY = 'tax-agent-key';
const DEFAULT_TENANT = 'acme-cn';
const DEFAULT_API_KEY = 'tax_demo_acme_cn_8f3a';

/** Known demo tenants (mirrors the backend _TENANTS store). */
export const KNOWN_TENANTS = ['acme-cn', 'acme-us', 'demo'] as const;

/** Dev demo keys — mirror TAX_TENANT_API_KEYS in .env.example.
 *  In production these would be issued per-user server-side, never shipped in JS. */
export const DEV_TENANT_KEYS: Record<string, string> = {
  'acme-cn': 'tax_demo_acme_cn_8f3a',
  'acme-us': 'tax_demo_acme_us_2b9e',
  'demo': 'tax_demo_demo_5c71',
};

/** Current tenant id, persisted in localStorage so switching survives reload. */
export function getTenantId(): string {
  if (typeof localStorage === 'undefined') return DEFAULT_TENANT;
  return localStorage.getItem(TENANT_STORAGE_KEY) || DEFAULT_TENANT;
}

/** Active API key (tenant is derived from this on the backend). */
export function getApiKey(): string {
  if (typeof localStorage === 'undefined') return DEFAULT_API_KEY;
  return localStorage.getItem(API_KEY_STORAGE_KEY) || DEFAULT_API_KEY;
}

export function setApiKey(key: string): void {
  if (typeof localStorage !== 'undefined') localStorage.setItem(API_KEY_STORAGE_KEY, key);
}

/** Switch the active tenant. Persists tenant + its credential; caller should reload. */
export function setTenantId(tenant: string): void {
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem(TENANT_STORAGE_KEY, tenant);
    const key = DEV_TENANT_KEYS[tenant];
    if (key) localStorage.setItem(API_KEY_STORAGE_KEY, key);
  }
}

export async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      'X-API-Key': getApiKey(),
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json();
}

export interface DashboardData {
  profile: { name: string; role: string } | null;
  recent_records: { id: number; status: string }[] | null;
  notifications: { message: string }[] | null;
}

export interface ToolInfo {
  id: number;
  tool_name: string;
  description: string;
  created_at: number;
}

export interface RAGResult {
  doc_id: string;
  content: string;
  score: number;
  metadata: Record<string, string>;
}

export interface RAGDocument {
  doc_id: string;
  title: string;
  source: string;
  effective_date: string | null;
  expiry_date: string | null;
  active: boolean;
  chunks: number;
}

export interface TokenUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  calls: number;
  cost: number;
  provider?: string;
}

export interface AgentResult {
  goal: string;
  results: {
    step: { step_id: number; description: string };
    verdict: string;
    evidence: string[];
    specialist_output?: string;
    independent_evidence?: string[];
    issues?: string[];
    verifications?: { criterion: string; verdict: string; reason: string }[];
  }[];
  total_steps: number;
  summary?: { total: number; pass: number; partial: number; fail: number };
  token_usage?: TokenUsage;
}

export interface TenantInfo {
  tenant_id: string;
  info: { tenant_id: string; name: string; plan: string; rate_limit: number } | null;
}

export interface LLMStatus {
  provider?: string;
  model: string;
  base_url: string;
  api_key_configured: boolean;
  llm_active?: boolean;
  circuit_breaker_threshold: number;
  llm_timeout: number;
}

export async function getRagDocuments(): Promise<RAGDocument[]> {
  return apiFetch<RAGDocument[]>('/rag/documents');
}

export async function reloadRag(): Promise<{ status: string; documents: number }> {
  return apiFetch<{ status: string; documents: number }>('/rag/reload', { method: 'POST' });
}
