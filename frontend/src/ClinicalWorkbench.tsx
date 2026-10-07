import { useEffect, useState } from 'react'
import { ApiFailure, getJson, type ContextReadout, type PreparedCandidate } from './api'
import './clinical-workbench.css'

function isPending(value: ContextReadout): boolean {
  return value.prepare_status === 'QUEUED' || value.prepare_status === 'RUNNING'
    || value.decision_status === 'QUEUED' || value.decision_status === 'RUNNING'
}

function CandidateGroup({ title, items }: { title: string; items: PreparedCandidate[] }) {
  return <section className="clinical-candidate-group">
    <div className="clinical-group-heading"><h3>{title}</h3><span>{items.length} 条</span></div>
    {items.map(item => <article className="clinical-candidate" key={item.candidate_id}>
      <div className="clinical-candidate-heading"><span>{item.regimen_code || '未映射方案'}</span><strong>{item.display_name || '方案名称待核对'}</strong></div>
      <div className="clinical-candidate-tags">
        {item.evidence_level !== null && <span>项目配置 Level {item.evidence_level}{item.evidence_grade ? ` · ${item.evidence_grade}` : ''}</span>}
        {item.data_labels.length > 0 && <span>{item.data_labels.length} 项数据提示</span>}
        {item.safety_labels.length > 0 && <span>{item.safety_labels.length} 项安全提示</span>}
        {item.x_reason_code && <span>X 理由：{item.x_reason_code}</span>}
      </div>
      <small>测试运行记录 · 尚未开放采用或患者方案保存</small>
    </article>)}
  </section>
}

export function ClinicalWorkbench({ contextId }: { contextId: string | null }) {
  const [readout, setReadout] = useState<ContextReadout | null>(null)
  const [loading, setLoading] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const [retry, setRetry] = useState(0)

  useEffect(() => {
    setReadout(null)
    setFailure(null)
    if (!contextId) { setLoading(false); return }
    const controller = new AbortController()
    let timer: number | undefined
    let disposed = false
    const read = async () => {
      setLoading(true)
      try {
        const result = await getJson<ContextReadout>(`/api/v1/contexts/${encodeURIComponent(contextId)}`, controller.signal)
        if (disposed || result.context_id.toLowerCase() !== contextId.toLowerCase()) return
        setReadout(result)
        setFailure(null)
        if (isPending(result)) timer = window.setTimeout(read, 2500)
      } catch (error) {
        if (!disposed && !(error instanceof DOMException && error.name === 'AbortError')) {
          setFailure(error instanceof ApiFailure ? error.message : '本次上下文读取失败')
        }
      } finally {
        if (!disposed) setLoading(false)
      }
    }
    void read()
    return () => { disposed = true; controller.abort(); if (timer !== undefined) window.clearTimeout(timer) }
  }, [contextId, retry])

  const expired = readout && (readout.context_state !== 'ACTIVE' || Date.parse(readout.expires_at) <= Date.now())
  const recommended = readout?.candidates.filter(item => item.presentation_region === 'RECOMMENDATION') ?? []
  const excluded = readout?.candidates.filter(item => item.presentation_region === 'X_EXCLUDED') ?? []
  const ready = !expired && readout?.prepare_status === 'SUCCEEDED' && readout.decision_status === 'SUCCEEDED'

  return <main className="clinical-workbench">
    <div className="workbench-title"><span className="eyebrow">CLINICAL WORKBENCH</span><h1>当前患者的方案候选</h1><p>院内工作站在进入或切换患者时后台准备；点开悬浮入口后读取本次已保存的状态与结果。</p></div>
    <section className="patient-band" aria-label="工作台患者上下文">
      <div className="patient-band-heading"><span className="mini-label">当前诊疗上下文</span><strong>{readout ? '合成测试上下文' : '尚无可信上下文'}</strong></div>
      <div className="patient-fact"><span>患者标识</span><strong>{readout?.patient_ref || '未接入'}</strong></div>
      <div className="patient-fact"><span>就诊标识</span><strong>{readout?.encounter_ref || '未接入'}</strong></div>
      <div className="patient-fact"><span>准备任务</span><strong>{readout?.prepare_status || '未启动'}</strong></div>
      <div className="patient-fact last"><span>决策运行</span><strong>{readout?.decision_status || '未生成'}</strong></div>
    </section>
    <div className="clinical-state-banner" role="status"><strong>测试读取能力</strong><span>仅允许连接独立测试库中的 TEST_ONLY 上下文；院方、模型和临床发布尚未接入。页面不会自行创建患者、任务或候选。</span></div>
    {!contextId && <section className="clinical-empty-state"><h2>等待院内工作站入口</h2><p>当前 URL 没有 `context_id`。医生正常使用时由工作站在后台触发准备并传入本次标识；公共方案库浏览不产生患者候选。</p></section>}
    {contextId && failure && <section className="clinical-empty-state" role="alert"><h2>无法读取本次上下文</h2><p>{failure}</p><button onClick={() => setRetry(value => value + 1)}>重新读取</button></section>}
    {contextId && !failure && loading && !readout && <section className="clinical-empty-state"><h2>正在读取准备状态</h2><p>请稍候。</p></section>}
    {readout && !failure && expired && <section className="clinical-empty-state"><h2>上下文已失效</h2><p>当前状态：{readout.context_state}。请从院内工作站重新进入这位患者；旧结果不会沿用到其他患者。</p></section>}
    {readout && !failure && !expired && readout.prepare_status === 'FAILED' && <section className="clinical-empty-state"><h2>后台准备失败</h2><p>错误代码：{readout.prepare_error_code || '未提供'}。不能把缺失的候选显示为临床不适用。</p></section>}
    {readout && !failure && !expired && isPending(readout) && <section className="clinical-empty-state"><h2>后台仍在准备</h2><p>当前阶段：{readout.prepare_stage || '等待运行'}。页面会继续读取本次任务；不会另起一轮或用旧患者结果填充。</p></section>}
    {readout && !failure && !expired && !isPending(readout) && readout.decision_status === 'FAILED' && <section className="clinical-empty-state"><h2>候选生成失败</h2><p>本次没有可展示的完整结果。</p></section>}
    {ready && <div className="clinical-results"><div className="clinical-results-heading"><div><span className="eyebrow">THIS ENCOUNTER / TEST ONLY</span><h2>本次候选</h2></div><span>{readout.candidates.length} 条 · {readout.outcome_code || '运行结果'}</span></div>{readout.candidates.length === 0 ? <div className="clinical-empty-state"><h3>本次没有已保存的候选</h3><p>这只表示本次测试运行没有候选记录，不能解释为临床不适用。</p></div> : <><CandidateGroup title="推荐区" items={recommended} /><CandidateGroup title="X 区" items={excluded} /></>}</div>}
    {readout && !failure && !expired && !isPending(readout) && !ready && readout.prepare_status !== 'FAILED' && readout.decision_status !== 'FAILED' && <section className="clinical-empty-state"><h2>等待本次候选结果</h2><p>准备状态：{readout.prepare_status || '未启动'}；决策状态：{readout.decision_status || '未启动'}。不从公共目录临时拼出患者候选。</p></section>}
  </main>
}
