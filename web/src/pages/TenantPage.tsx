import { useState, useEffect } from 'react';
import { apiFetch, getTenantId, setTenantId, KNOWN_TENANTS, type TenantInfo } from '../api';

export default function TenantPage() {
  const [info, setInfo] = useState<TenantInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const current = getTenantId();

  useEffect(() => {
    apiFetch<TenantInfo>('/tenant/me')
      .then(setInfo)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const switchTenant = (t: string) => {
    if (t === current) return;
    setTenantId(t);
    // Reload so every page re-fetches under the new tenant (isolated RAG/tools).
    window.location.reload();
  };

  if (loading) return <p className="loading">Loading tenant...</p>;
  if (error) return <p className="error">Error: {error}</p>;

  return (
    <>
      <h1 className="page-title">Tenant Context</h1>
      <p className="page-desc"> contextvars tenant isolation + token bucket rate limiting + RLS</p>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-title">Switch Tenant (data isolation demo)</div>
        <div className="example-chips" style={{ marginTop: 8 }}>
          {KNOWN_TENANTS.map((t) => (
            <button
              key={t}
              className="chip"
              onClick={() => switchTenant(t)}
              disabled={t === current}
              style={t === current ? { borderColor: 'var(--primary-light)', color: 'var(--primary-light)' } : undefined}
            >
              {t}{t === current ? ' ✓' : ''}
            </button>
          ))}
        </div>
        <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 8 }}>
          切换后页面刷新，RAG 知识库与工具注册表按租户隔离（如 acme-us 有独立的美国联邦税语料）。
        </div>
      </div>

      <div className="grid-3">
        <div className="card">
          <div className="card-title">Tenant ID</div>
          <div style={{ fontSize: 18, fontWeight: 600 }}>
            <span className="status-dot online" />
            {info?.tenant_id}
          </div>
        </div>

        <div className="card">
          <div className="card-title">Tenant Info</div>
          {info?.info ? (
            <>
              <div><strong>Name:</strong> {info.info.name}</div>
              <div><strong>Plan:</strong> {info.info.plan}</div>
              <div><strong>Rate Limit:</strong> {info.info.rate_limit} req/min</div>
            </>
          ) : (
            <div style={{ color: 'var(--warning)' }}>Unknown tenant</div>
          )}
        </div>

        <div className="card">
          <div className="card-title">Context Propagation</div>
          <div style={{ fontSize: 13, color: 'var(--text-muted)' }}>
            <div>API Key → tenant (derived, not claimed)</div>
            <div>contextvars.Context → handlers</div>
            <div>Token Bucket: {info?.info?.rate_limit || 0} tokens/min</div>
            <div>RLS: row-level data isolation</div>
          </div>
        </div>
      </div>
    </>
  );
}
