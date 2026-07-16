import { useState } from 'react';
import Dashboard from './pages/Dashboard';
import AgentPage from './pages/AgentPage';
import RAGPage from './pages/RAGPage';
import ToolsPage from './pages/ToolsPage';
import TenantPage from './pages/TenantPage';

type Page = 'dashboard' | 'agent' | 'rag' | 'tools' | 'tenant';

const NAV_ITEMS: { key: Page; label: string; icon: string }[] = [
  { key: 'dashboard', label: 'Dashboard', icon: '📊' },
  { key: 'agent', label: 'Agent', icon: '🤖' },
  { key: 'rag', label: 'RAG', icon: '🔍' },
  { key: 'tools', label: 'Tools', icon: '🔧' },
  { key: 'tenant', label: 'Tenant', icon: '🏢' },
];

const PAGES: Record<Page, React.FC> = {
  dashboard: Dashboard,
  agent: AgentPage,
  rag: RAGPage,
  tools: ToolsPage,
  tenant: TenantPage,
};

function App() {
  const [page, setPage] = useState<Page>('dashboard');
  const PageComponent = PAGES[page];

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="sidebar-logo">Tax Agent</div>
        <ul className="sidebar-nav">
          {NAV_ITEMS.map((item) => (
            <li
              key={item.key}
              className={page === item.key ? 'active' : ''}
              onClick={() => setPage(item.key)}
            >
              <span>{item.icon}</span> {item.label}
            </li>
          ))}
        </ul>
      </aside>
      <main className="main">
        <PageComponent />
      </main>
    </div>
  );
}

export default App;
