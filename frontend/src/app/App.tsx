import { useEffect, useState } from 'react'
import { ClinicalWorkbench } from '../pages/workstation/ClinicalWorkbench'
import { ManagementPage } from '../pages/management/ManagementPage'
import { CatalogPage } from '../pages/catalog/CatalogPage'
import { connectHost } from '../integrations/host'
import { getCapabilities } from '../api/system'
import type { CapabilityStatus } from '../types/contracts'
import { Badge, Icon } from '../components/ui'

function App() {
  type Area = 'catalog' | 'workbench' | 'evidence' | 'operations'
  const areaFromLocation = (): Area => ({ '/catalog': 'catalog', '/evidence': 'evidence', '/operations': 'operations' } as Record<string, Area>)[window.location.pathname] || 'workbench'
  const [activeArea, setActiveArea] = useState<Area>(areaFromLocation)
  const [contextId, setContextId] = useState<string | null>(new URLSearchParams(window.location.search).get('context_id'))
  const [authGeneration, setAuthGeneration] = useState(0)
  const [status, setStatus] = useState<CapabilityStatus | null>(null)


  useEffect(() => {
    let dispose: (() => void) | undefined
    let cancelled = false
    void connectHost(id => { if (!cancelled) { setContextId(id); setAuthGeneration(value => value + 1); setActiveArea('workbench') } }).then(stop => { if (cancelled) stop(); else dispose = stop })
    return () => { cancelled = true; dispose?.() }
  }, [])

  useEffect(() => {
    const onLocation = () => {
      setActiveArea(areaFromLocation())
      setContextId(new URLSearchParams(window.location.search).get('context_id'))
    }
    window.addEventListener('popstate', onLocation)
    return () => window.removeEventListener('popstate', onLocation)
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    getCapabilities(controller.signal).then(setStatus).catch(() => setStatus(null))
    return () => controller.abort()
  }, [])

  const changeArea = (area: Area) => {
    window.history.pushState(null, '', area === 'workbench' ? contextId ? `/?context_id=${encodeURIComponent(contextId)}` : '/' : `/${area}`)
    setActiveArea(area)
  }


  return <div className="app-shell">
    <aside className="rail" aria-label="主导航">
      <div className="brand-symbol" aria-label="化疗智能体"><span className="brand-cross">✳</span></div>
      <nav className="rail-nav">
        <button className={`rail-item ${activeArea === 'workbench' ? 'active' : ''}`} aria-current={activeArea === 'workbench' ? 'page' : undefined} title="患者工作台" onClick={() => changeArea('workbench')}><Icon name="book" /><span>工作台</span></button>
        <button className={`rail-item ${activeArea === 'catalog' ? 'active' : ''}`} aria-current={activeArea === 'catalog' ? 'page' : undefined} title="方案与证据浏览" onClick={() => changeArea('catalog')}><Icon name="grid" /><span>浏览</span></button>
        <button className={`rail-item ${activeArea === 'evidence' ? 'active' : ''}`} aria-current={activeArea === 'evidence' ? 'page' : undefined} title="知识与证据核对" onClick={() => changeArea('evidence')}><Icon name="layers" /><span>证据</span></button>
        <button className={`rail-item ${activeArea === 'operations' ? 'active' : ''}`} aria-current={activeArea === 'operations' ? 'page' : undefined} title="运行管理" onClick={() => changeArea('operations')}><Icon name="activity" /><span>运行</span></button>
      </nav>
      <div className="rail-bottom"><span className="rail-dot" /> V0.1</div>
    </aside>

    <div className="shell-content">
      <header className="topbar">
        <div className="breadcrumb"><span>化疗智能体</span><Icon name="chevron" size={14} /><strong>{{ catalog: '方案与证据浏览', workbench: '患者工作台', evidence: '知识与证据核对', operations: '运行管理' }[activeArea]}</strong></div>
        <div className="topbar-right"><Badge tone="quiet">内部测试</Badge><span className="topbar-separator" /><span className="system-state"><span className={`status-dot ${status?.database === 'CONNECTED' ? 'is-on' : ''}`} />方案库{status?.database === 'CONNECTED' ? '已连接' : '未连接'}</span></div>
      </header>

      {activeArea === 'workbench' ? <ClinicalWorkbench key={`${contextId || 'no-context'}:${authGeneration}`} contextId={contextId} /> : activeArea === 'evidence' || activeArea === 'operations' ? <ManagementPage key={`${activeArea}:${authGeneration}`} area={activeArea} /> : <CatalogPage key={authGeneration} />}
    </div>

  </div>
}

export default App
