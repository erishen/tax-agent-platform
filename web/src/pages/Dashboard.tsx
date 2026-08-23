import { useState, useEffect } from 'react';
import { apiFetch, type DashboardData, type LLMStatus } from '../api';

export default function Dashboard() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [llm, setLLM] = useState<LLMStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    //  asyncio.gather — frontend mirrors backend concurrent fetch
    Promise.all([
      apiFetch<DashboardData>('/dashboard/1'),
      apiFetch<LLMStatus>('/llm/status'),
    ])
      .then(([dash, llmData]) => {
        setData(dash);
        setLLM(llmData);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <p className="loading">Loading dashboard...</p>;
  if (error) return <p className="error">Error: {error}</p>;

  return (
    <>
      <h1 className="page-title">Dashboard</h1>
      <p className="page-desc"> asyncio.gather concurrent data fetching with timeout fallback</p>

      <div className="grid-3">
        <div className="card">
          <div className="card-title">Profile</div>
          {data?.profile ? (
            <>
              <div style={{ fontSize: 18, fontWeight: 600 }}>{data.profile.name}</div>
              <div style={{ color: 'var(--text-muted)' }}>{data.profile.role}</div>
            </>
          ) : (
            <div style={{ color: 'var(--warning)' }}>Profile unavailable (fallback)</div>
          )}
        </div>

        <div className="card">
          <div className="card-title">Recent Records</div>
          {data?.recent_records ? (
            data.recent_records.map((r) => (
              <div key={r.id} style={{ marginBottom: 4 }}>
                <span className={`tag tag-${r.status === 'Filed' ? 'pass' : 'partial'}`}>
                  {r.status}
                </span>{' '}
                #{r.id}
              </div>
            ))
          ) : (
            <div style={{ color: 'var(--warning)' }}>Records unavailable (fallback)</div>
          )}
        </div>

        <div className="card">
          <div className="card-title">LLM Status</div>
          {llm ? (
            <div>
              <div><strong>Model:</strong> {llm.model}</div>
              <div><strong>Base URL:</strong> {llm.base_url}</div>
              <div>
                <strong>API Key:</strong>{' '}
                <span className={`tag ${llm.api_key_configured ? 'tag-pass' : 'tag-fail'}`}>
                  {llm.api_key_configured ? 'Configured' : 'Missing'}
                </span>
              </div>
              <div><strong>Circuit Breaker:</strong> {llm.circuit_breaker_threshold} failures</div>
              <div><strong>Timeout:</strong> {llm.llm_timeout}s</div>
            </div>
          ) : (
            <div style={{ color: 'var(--warning)' }}>LLM status unavailable</div>
          )}
        </div>
      </div>

      <div className="card">
        <div className="card-title">Notifications</div>
        {data?.notifications ? (
          data.notifications.map((n, i) => (
            <div key={i} style={{ padding: '4px 0' }}>{n.message}</div>
          ))
        ) : (
          <div style={{ color: 'var(--warning)' }}>Notifications unavailable (fallback)</div>
        )}
      </div>
    </>
  );
}
