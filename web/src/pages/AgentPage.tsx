import { useState, useRef } from 'react';
import { getApiKey, type AgentResult } from '../api';

const API_BASE = '/api';

export default function AgentPage() {
  const [goal, setGoal] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AgentResult | null>(null);
  const [error, setError] = useState('');
  const [logs, setLogs] = useState<string[]>([]);
  const evtSourceRef = useRef<EventSource | null>(null);

  const handleRun = async () => {
    if (!goal.trim()) return;
    setLoading(true);
    setError('');
    setResult(null);
    setLogs([]);

    // SSE stream drives BOTH the live logs and the final result, so the agent
    // runs exactly once — no second /agent/run call (which would double-spend
    // LLM tokens). The backend emits a `result` event with the full payload
    // just before `done`.
    try {
      // EventSource can't set headers, so pass the API key as a query param.
      // The backend derives the tenant from the key (?api_key=), ignoring any
      // client-claimed tenant — this is what keeps tenant isolation real.
      const evtSource = new EventSource(
        `${API_BASE}/agent/stream?goal=${encodeURIComponent(goal)}&api_key=${encodeURIComponent(getApiKey())}`,
      );
      evtSourceRef.current = evtSource;

      evtSource.addEventListener('planning', (e) => {
        const data = JSON.parse(e.data);
        setLogs((prev) => [...prev, `[planning] ${data.status}${data.goal ? ': ' + data.goal : ''}`]);
      });

      evtSource.addEventListener('executing', (e) => {
        const data = JSON.parse(e.data);
        setLogs((prev) => [...prev, `[executing] Step ${data.step || '?'} — ${data.status}${data.evidence_count ? ` (${data.evidence_count} evidence)` : ''}`]);
      });

      evtSource.addEventListener('evaluating', (e) => {
        const data = JSON.parse(e.data);
        setLogs((prev) => [...prev, `[evaluating] Step ${data.step || '?'} → ${data.verdict}${data.issues?.length ? ' Issues: ' + data.issues.join(', ') : ''}`]);
      });

      evtSource.addEventListener('result', (e) => {
        setResult(JSON.parse(e.data) as AgentResult);
      });

      evtSource.addEventListener('done', (e) => {
        const data = JSON.parse(e.data);
        evtSource.close();
        evtSourceRef.current = null;
        setLogs((prev) => [...prev, `[done] ${data.status} — ${data.total_steps} steps`]);
        setLoading(false);
      });

      evtSource.onerror = () => {
        evtSource.close();
        evtSourceRef.current = null;
        setLoading(false);
      };
    } catch {
      // EventSource unavailable in this environment.
      setLoading(false);
    }
  };

  const handleStop = () => {
    evtSourceRef.current?.close();
    evtSourceRef.current = null;
    setLoading(false);
  };

  const getLogClass = (log: string) => {
    if (log.includes('[planning]')) return 'log-planning';
    if (log.includes('[executing]')) return 'log-executing';
    if (log.includes('[evaluating]')) return 'log-evaluating';
    if (log.includes('[done]')) return 'log-done';
    return '';
  };

  return (
    <>
      <h1 className="page-title">Agent Workflow</h1>
      <p className="page-desc"> PSE (Planner-Specialist-Evaluator) three-role agent with evidence-driven verification + SSE streaming</p>

      <div className="input-group">
        <input
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleRun()}
          placeholder="Enter agent goal, e.g. 'Verify VAT compliance for 2024'"
        />
        {loading ? (
          <button onClick={handleStop} style={{ background: 'var(--danger)' }}>Stop</button>
        ) : (
          <button onClick={handleRun} disabled={!goal.trim()}>Run Agent</button>
        )}
      </div>

      {error && <p className="error">{error}</p>}

      {logs.length > 0 && (
        <div className="card">
          <div className="card-title">SSE Log Stream</div>
          <div className="sse-log">
            {logs.map((log, i) => (
              <div key={i} className={getLogClass(log)}>{log}</div>
            ))}
          </div>
        </div>
      )}

      {result && (
        <>
          {result.token_usage && (
            <div className="card" style={{ marginBottom: 16 }}>
              <div className="card-title">Token Usage &amp; Cost</div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(110px, 1fr))', gap: 12 }}>
                {[
                  ['Total tokens', result.token_usage.total_tokens.toLocaleString()],
                  ['Prompt', result.token_usage.prompt_tokens.toLocaleString()],
                  ['Completion', result.token_usage.completion_tokens.toLocaleString()],
                  ['LLM calls', String(result.token_usage.calls)],
                  ['Est. cost', '¥' + result.token_usage.cost.toFixed(4)],
                ].map(([label, val]) => (
                  <div key={label} style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, padding: '10px 12px' }}>
                    <div style={{ fontSize: 20, fontWeight: 600 }}>{val}</div>
                    <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>{label}</div>
                  </div>
                ))}
              </div>
              {result.token_usage.provider && (
                <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 8 }}>
                  Provider: {result.token_usage.provider}
                </div>
              )}
            </div>
          )}

          <div className="card">
            <div className="card-title">Result — {result.total_steps} Steps</div>
          {result.results?.map((r, i) => (
            <div key={i} style={{ marginBottom: 16, paddingBottom: 16, borderBottom: '1px solid var(--border)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                <strong>Step {r.step.step_id}:</strong> {r.step.description}
                <span className={`tag tag-${r.verdict.toLowerCase()}`}>
                  {r.verdict}
                </span>
              </div>
              {r.specialist_output && (
                <div style={{ marginBottom: 12 }}>
                  <div className="card-subtitle">Specialist Answer (RAG-grounded)</div>
                  <div style={{
                    background: 'var(--surface)',
                    border: '1px solid var(--border)',
                    borderRadius: 6,
                    padding: '10px 12px',
                    whiteSpace: 'pre-wrap',
                    fontSize: 14,
                    lineHeight: 1.6,
                  }}>{r.specialist_output}</div>
                </div>
              )}

              <ul className="evidence-list">
                {r.evidence?.map((e, j) => (
                  <li key={j}>{e}</li>
                ))}
              </ul>

              {r.verifications && r.verifications.length > 0 && (
                <div style={{ marginTop: 12 }}>
                  <div className="card-subtitle">Independent Verification (Evaluator LLM audit)</div>
                  <ul className="evidence-list">
                    {r.verifications.map((v, j) => (
                      <li key={j}>
                        <span className={`tag tag-${v.verdict.toLowerCase()}`}>{v.verdict}</span>{' '}
                        <strong>{v.criterion}</strong>
                        {v.reason && <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>{v.reason}</div>}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {r.independent_evidence && r.independent_evidence.length > 0 && (
                <details style={{ marginTop: 8 }}>
                  <summary style={{ cursor: 'pointer', fontSize: 13, color: 'var(--text-muted)' }}>
                    Independent evidence re-collected by Evaluator ({r.independent_evidence.length})
                  </summary>
                  <ul className="evidence-list" style={{ marginTop: 6 }}>
                    {r.independent_evidence.map((e, j) => (
                      <li key={j} style={{ fontSize: 12 }}>{e}</li>
                    ))}
                  </ul>
                </details>
              )}

              {r.issues && r.issues.length > 0 && (
                <div style={{ marginTop: 8, color: 'var(--warning)', fontSize: 13 }}>
                  Issues: {r.issues.join('; ')}
                </div>
              )}
            </div>
          ))}
        </div>
      </>
      )}
    </>
  );
}
