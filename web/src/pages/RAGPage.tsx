import { useState, useEffect } from 'react';
import {
  apiFetch,
  getRagDocuments,
  reloadRag,
  type RAGResult,
  type RAGDocument,
} from '../api';

const EXAMPLES = ['软件增值税税率', '企业所得税', '增值税', 'CIT rate', '研发费用加计扣除'];

export default function RAGPage() {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<RAGResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [searched, setSearched] = useState(false);
  const [activeOnly, setActiveOnly] = useState(true);

  const [docs, setDocs] = useState<RAGDocument[]>([]);
  const [docsLoading, setDocsLoading] = useState(false);
  const [reloadMsg, setReloadMsg] = useState('');

  const loadDocs = async () => {
    setDocsLoading(true);
    try {
      setDocs(await getRagDocuments());
    } catch (e: any) {
      setError(e.message);
    } finally {
      setDocsLoading(false);
    }
  };

  useEffect(() => {
    loadDocs();
  }, []);

  const handleReload = async () => {
    setReloadMsg('');
    try {
      const r = await reloadRag();
      setReloadMsg(`已重新加载，当前 ${r.documents} 篇文档`);
      await loadDocs();
    } catch (e: any) {
      setReloadMsg(`重载失败: ${e.message}`);
    }
  };

  const handleSearch = async (q?: string) => {
    const question = (q ?? query).trim();
    if (!question) return;
    setQuery(question);
    setLoading(true);
    setError('');
    setSearched(false);
    try {
      // Backend: GET /rag/query?question=...&top_k=...
      const data = await apiFetch<RAGResult[]>(
        `/rag/query?question=${encodeURIComponent(question)}&top_k=10&active_only=${activeOnly}`,
      );
      setResults(data || []);
      setSearched(true);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <h1 className="page-title">RAG Retrieval</h1>
      <p className="page-desc">Q5: Semantic chunking + two-stage retrieval + Cross-Encoder reranking + recency filter</p>

      <div className="input-group">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
          placeholder="Enter tax regulation query..."
        />
        <button onClick={() => handleSearch()} disabled={loading}>
          {loading ? 'Searching...' : 'Search'}
        </button>
      </div>

      <div className="example-chips">
        {EXAMPLES.map((ex) => (
          <button key={ex} className="chip" onClick={() => handleSearch(ex)} disabled={loading}>
            {ex}
          </button>
        ))}
      </div>

      <label className="rag-toggle">
        <input
          type="checkbox"
          checked={activeOnly}
          onChange={(e) => setActiveOnly(e.target.checked)}
        />
        仅检索生效中文档（取消勾选可对比已废止的旧规，如增值税2022）
      </label>

      {error && <p className="error">{error}</p>}

      {/* Knowledge Base panel */}
      <div className="card">
        <div className="card-title" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span>Knowledge Base ({docs.length} docs)</span>
          <button onClick={handleReload} disabled={docsLoading} style={{ fontSize: 12 }}>
            {docsLoading ? 'Loading...' : 'Reload'}
          </button>
        </div>
        {reloadMsg && (
          <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '4px 0 0' }}>{reloadMsg}</p>
        )}
        {docs.map((d) => (
          <div key={d.doc_id} style={{ padding: '8px 0', borderBottom: '1px solid var(--border)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <span className={d.active ? 'status-active' : 'status-superseded'}>
                {d.active ? '生效中' : '已废止'}
              </span>
              <strong>{d.title}</strong>
              <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>· {d.doc_id}</span>
            </div>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>
              {d.source && <span>来源: {d.source}</span>}
              {d.effective_date && <span> · 生效: {d.effective_date}</span>}
              {d.expiry_date && <span> · 废止: {d.expiry_date}</span>}
              <span> · {d.chunks} chunks</span>
            </div>
          </div>
        ))}
      </div>

      {searched && !loading && results.length === 0 && (
        <div className="card">
          <p style={{ color: 'var(--text-muted)' }}>
            No matching documents. Try a tax-related term such as “软件增值税税率” or “企业所得税”.
          </p>
        </div>
      )}

      {results.length > 0 && (
        <div className="card">
          <div className="card-title">Results ({results.length})</div>
          {results.map((r, i) => (
            <div key={i} className="rag-result">
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
                <span className="rag-score">{r.score.toFixed(4)}</span>
                <strong>{r.metadata.doc_title || r.doc_id}</strong>
                <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>· {r.doc_id}</span>
              </div>
              <div style={{ fontSize: 12, color: 'var(--text-muted)', margin: '2px 0 6px' }}>
                {r.metadata.source && <span>来源: {r.metadata.source}</span>}
                {r.metadata.effective_date && r.metadata.effective_date !== 'None' && (
                  <span> · 生效: {r.metadata.effective_date}</span>
                )}
              </div>
              <p style={{ color: 'var(--text-muted)', fontSize: 13, margin: 0 }}>
                {r.content.slice(0, 300)}{r.content.length > 300 ? '...' : ''}
              </p>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
