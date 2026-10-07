import { useEffect, useState } from 'react'
import { ApiFailure, getJson } from './api'
import './management-page.css'

interface CountRow { status?: string; stage?: string; source?: string; internal_level?: number; evidence_grade?: string; package_key?: string; job_kind?: string; error_code?: string; operation_name?: string; transport_outcome?: string; count: number }
interface Overview { evidence?: CountRow[]; applicability?: CountRow[]; rule_packages?: CountRow[]; unlinked_evidence?: number; jobs?: CountRow[]; preparations?: CountRow[]; agents?: CountRow[]; hospital_calls?: CountRow[]; model_configured?: boolean; hospital_read_configured?: boolean; hospital_delivery_configured?: boolean }
const labels: Record<string, string> = { DRAFT: '待核验', REVIEWED: '已记录审核', PUBLISHED: '已发布', RETIRED: '已停用', QUEUED: '排队中', RUNNING: '运行中', SUCCEEDED: '已完成', FAILED: '失败', CANCELLED: '已取消', SUPERSEDED: '已被更新替代', DEAD_LETTER: '需人工检查', RETRY_WAIT: '等待重试', PREPARE: '后台准备', AGENT_RUN: '智能体运行', STARTED: '已发起', RESPONDED: '已收到响应', TIMEOUT: '调用超时', NETWORK_ERROR: '网络失败' }
function CountTable({ title, rows }: { title: string; rows?: CountRow[] }) {
  return <section className="management-section"><h2>{title}</h2>{rows?.length ? <table><thead><tr><th>类型</th><th>状态</th><th>数量</th></tr></thead><tbody>{rows.map((row, i) => <tr key={i}><td>{row.source || row.package_key || labels[row.job_kind || ''] || row.operation_name || '记录'}{row.internal_level != null && <small>Level {row.internal_level}{row.evidence_grade && ` · ${row.evidence_grade}`}</small>}{row.error_code && <small>{row.error_code}</small>}</td><td>{labels[row.status || row.transport_outcome || ''] || '待确认'}</td><td>{row.count}</td></tr>)}</tbody></table> : <p className="management-empty">暂无实际记录</p>}</section>
}
export function ManagementPage({ area }: { area: 'evidence' | 'operations' }) {
  const [data, setData] = useState<Overview | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setFailure(null); setData(null)
    void getJson<Overview>(`/api/v1/${area === 'evidence' ? 'knowledge' : 'operations'}/overview`, controller.signal).then(value => { if (!controller.signal.aborted) setData(value) }).catch(error => { if (!controller.signal.aborted) setFailure(error instanceof ApiFailure ? error.message : '管理数据读取失败') }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [area, retry])
  return <main className="management-page"><header><div><span className="eyebrow">{area === 'evidence' ? 'KNOWLEDGE / REVIEW' : 'OPERATIONS / STATUS'}</span><h1>{area === 'evidence' ? '知识与证据核对' : '运行管理'}</h1><p>{area === 'evidence' ? '查看来源、关联和审核数量。发布须有真实审核记录，当前入口不自动发布证据。' : '仅显示当前授权医院的实际任务和调用状态。模型配置与院方连接分别记录。'}</p></div><button onClick={() => setRetry(value => value + 1)} disabled={loading}>刷新记录</button></header>
    {loading && <div className="management-state" role="status">正在读取实际记录…</div>}{failure && <div className="management-state" role="alert"><h2>当前入口需要对应权限</h2><p>{failure}</p><small>{area === 'evidence' ? '需要知识维护人员授权；方案与证据的公共查看可从“浏览”进入。' : '需要当前医院的运行管理权限。'}</small></div>}
    {data && (area === 'evidence' ? <><div className="management-status-line"><strong>未关联证据 {data.unlinked_evidence ?? 0} 条</strong><span>导入和审核分开记录</span></div><div className="management-grid"><CountTable title="证据来源与等级" rows={data.evidence} /><CountTable title="方案适用关系" rows={data.applicability} /><CountTable title="版本化规则包" rows={data.rule_packages} /></div></> : <><div className="management-status-line"><span>模型：{data.model_configured ? '已配置，待调用验证' : '未接入'}</span><span>院方读取：{data.hospital_read_configured ? '已配置，待联通验证' : '未接入'}</span><span>院方交付：{data.hospital_delivery_configured ? '已配置，仍需临床授权' : '未接入'}</span></div><div className="management-grid"><CountTable title="后台任务" rows={data.jobs} /><CountTable title="患者准备" rows={data.preparations} /><CountTable title="智能体运行" rows={data.agents} /><CountTable title="院方实际调用" rows={data.hospital_calls} /></div></>)}
  </main>
}
