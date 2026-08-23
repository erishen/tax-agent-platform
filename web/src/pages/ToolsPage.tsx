import { useState, useEffect } from 'react';
import { apiFetch, type ToolInfo } from '../api';

export default function ToolsPage() {
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const fetchTools = () => {
    apiFetch<ToolInfo[]>('/tools/')
      .then(setTools)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(fetchTools, []);

  if (loading) return <p className="loading">Loading tools...</p>;
  if (error) return <p className="error">Error: {error}</p>;

  return (
    <>
      <h1 className="page-title">Tool Registry</h1>
      <p className="page-desc"> Tool discovery and FastAPI dependency injection</p>

      <div className="card">
        <div className="card-title">Registered Tools ({tools.length})</div>
        {tools.map((t) => (
          <div key={t.id} className="tool-item">
            <div>
              <strong>{t.tool_name}</strong>
              <div style={{ color: 'var(--text-muted)', fontSize: 13 }}>{t.description}</div>
            </div>
            <div style={{ color: 'var(--text-muted)', fontSize: 12 }}>
              #{t.id}
            </div>
          </div>
        ))}
        {tools.length === 0 && (
          <p style={{ color: 'var(--text-muted)' }}>No tools registered</p>
        )}
      </div>
    </>
  );
}
