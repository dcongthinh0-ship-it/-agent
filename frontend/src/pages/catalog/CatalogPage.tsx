import { PatientEditor } from '../../features/patient-regimen/PatientEditor'
import { Badge, Failure, Icon } from '../../components/ui'
import { useCatalog } from './useCatalog'

export function CatalogPage() {
  const { query, setQuery, page, setPage, catalog, catalogLoading, catalogError, setCatalogRetry, selected, detail, evidence, detailLoading, detailError, setDetailRetry, drawerItem, setDrawerItem, drawerDetail, drawerError, mobileDetail, setMobileDetail, formMode, setFormMode, choose, totalPages } = useCatalog()
  return <>
<main>
        <div className="page-intro">
          <div><p className="eyebrow">CATALOG REVIEW / 01</p><h1>方案与证据浏览<span className="title-mark">.</span></h1><p className="page-subtitle">核对固定版本与来源，当前不进行患者决策。</p></div>
          <div className="intro-side"><span className="intro-side-label">当前能力</span><strong>方案与证据 · 只读浏览</strong><span>模型、医院接口和临床推荐尚未接入</span></div>
        </div>

        <div className="notice" role="status"><Icon name="info" size={18} /><div><strong>公共方案库 · 只读浏览</strong><span>查看方案内容、固定版本和关联证据；查看不会创建患者方案。</span></div></div>

        <div className={`workspace ${mobileDetail ? 'show-mobile-detail' : ''}`}>
          <section className="master-pane" aria-labelledby="catalog-heading">
            <div className="pane-heading"><div><p className="eyebrow">REGIMEN CATALOG</p><h2 id="catalog-heading">方案目录 <span className="count">{catalog?.total ?? '—'}</span></h2></div><span className="pane-hint">固定版本</span></div>
            <label className="searchbox"><Icon name="search" size={19} /><span className="sr-only">搜索方案名称、编码或癌种</span><input value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索方案 / 编码 / 癌种" /></label>
            <div className="list-caption"><span>全部方案</span><span>{catalogLoading ? '读取中' : `${catalog?.items.length ?? 0} 条 / 本页`}</span></div>
            <div className="regimen-list">
              {catalogLoading && Array.from({ length: 6 }, (_, i) => <div className="list-skeleton" key={i}><span /><span /><span /></div>)}
              {catalogError && <Failure message={catalogError} onRetry={() => setCatalogRetry(x => x + 1)} />}
              {!catalogLoading && !catalogError && catalog?.items.length === 0 && <div className="state"><Icon name="search" size={25} /><strong>没有找到匹配方案</strong><p>检查名称、编码或癌种，也可以清空搜索。</p><button className="text-button" onClick={() => setQuery('')}>清空搜索</button></div>}
              {!catalogLoading && !catalogError && catalog?.items.map(item => <article className={`regimen-card ${selected?.version_id === item.version_id ? 'selected' : ''}`} key={item.version_id}>
                <div className="regimen-card-top"><span className="code">{item.regimen_code}</span></div>
                <h3>{item.display_name}</h3><p>{item.cancer_category ?? '癌种未配置'}</p>
                <div className="regimen-card-bottom"><span><Icon name="file" size={15} /> {item.evidence_link_count} 条关联证据</span><button onClick={() => choose(item)} aria-label={`查看 ${item.display_name} 的固定版本详情`}>查看详情 <Icon name="chevron" size={15} /></button></div>
              </article>)}
            </div>
            <div className="pagination"><button disabled={page <= 1 || catalogLoading} onClick={() => setPage(page - 1)} aria-label="上一页"><Icon name="arrow" size={17} /></button><span>{page} / {totalPages}</span><button disabled={page >= totalPages || catalogLoading} onClick={() => setPage(page + 1)} aria-label="下一页"><Icon name="chevron" size={17} /></button></div>
          </section>

          <section className="detail-pane" aria-label="方案固定版本详情">
            <button className="mobile-back" onClick={() => setMobileDetail(false)}><Icon name="arrow" size={18} /> 返回方案列表</button>
            {!selected && <div className="detail-placeholder"><span className="placeholder-symbol"><Icon name="book" size={36} /></span><p className="eyebrow">A CLOSER LOOK</p><h2>选择一份方案<br />查看固定版本与证据。</h2><p>目录浏览不产生推荐、采用或保存动作。</p><div className="placeholder-line" /></div>}
            {selected && detailLoading && <div className="detail-loading"><div className="shimmer large" /><div className="shimmer" /><div className="shimmer" /><p>正在读取固定版本及关联证据…</p></div>}
            {selected && detailError && <Failure message={detailError} onRetry={() => setDetailRetry(x => x + 1)} />}
            {detail && !detailLoading && !detailError && <div className="detail-content">
              <div className="detail-topline"><div><span className="eyebrow">FIXED VERSION / {detail.regimen_code}</span><h2>{detail.display_name}</h2><div className="detail-meta"><span>版本 {detail.version_no}</span><span>·</span><span>{detail.cancer_category ?? '癌种未配置'}</span></div></div><span className="detail-index">{String(detail.version_no).padStart(2, '0')}</span></div>
              <div className="detail-warning"><Icon name="shield" size={18} /><span>该方案尚未完成发布审核；本页仅用于核对现有数据，不提供临床采用。</span></div>
              <div className="detail-actions"><button className="button-secondary editor-entry" onClick={() => setFormMode('view')}><Icon name="file" size={17} /> 查看完整方案表单</button></div>

              <section className="detail-section"><div className="section-title"><div><span className="section-number">01</span><h3>方案组成</h3></div><span>{detail.medications.length} 条药品行</span></div>
                {detail.medications.length === 0 ? <p className="muted">该版本暂无结构化药品行，请查看完整方案表单。</p> : <div className="medication-list">{detail.medications.map((med, i) => <div className="medication-row" key={med.item_key}><span className="med-index">{String(i + 1).padStart(2, '0')}</span><div className="med-main"><strong>{med.generic_name || med.source_drug_name}</strong>{med.generic_name && med.generic_name !== med.source_drug_name && <small>来源药名：{med.source_drug_name}</small>}<span>{med.standard_dose_text || '剂量未结构化'} · {med.route_text || '途径未配置'} · {med.administration_day_text || '日期未配置'}</span></div><Badge tone={med.frequency_text ? 'quiet' : 'warning'}>{med.frequency_text || '频次未配置'}</Badge></div>)}</div>}
              </section>

              <section className="detail-section evidence-section"><div className="section-title"><div><span className="section-number">02</span><h3>关联证据</h3></div><span>{evidence?.items.length ?? 0} 条</span></div>
                {!evidence?.items.length ? <div className="inline-empty"><Icon name="info" size={19} /><span>该固定版本暂无关联证据；不能解释为临床不适用。</span></div> : <div className="evidence-list">{evidence.items.map(item => <div className="evidence-row" key={item.association_id}><div className="evidence-source"><span className="source-mark">{item.display_source}</span><span>{item.association_scope === 'DRUG_ONLY' ? '单药依据' : '联合方案依据'}</span></div><div className="evidence-body"><div>{item.internal_level !== null && <span className="level">项目配置 Level {item.internal_level} · {item.evidence_grade ?? '等级待核'}</span>}</div><strong>{item.source_title || `${item.display_source} 来源资料`}</strong><p>{item.excerpt_preview || '原文尚未提取'}</p><button className="text-button" onClick={() => setDrawerItem(item)}>查看原文与来源 <Icon name="chevron" size={16} /></button></div></div>)}</div>}
                {evidence?.truncated && <p className="muted">关联证据超过当前读取上限；请使用维护入口核对完整清单。</p>}
              </section>
            </div>}
          </section>
        </div>
      </main>
    {formMode && detail && <PatientEditor key={`${detail.version_id}-${formMode}`} detail={detail} mode={formMode} onClose={() => setFormMode(null)} />}
    {drawerItem && <div className="drawer-layer"><button className="drawer-scrim" aria-label="关闭证据详情" onClick={() => setDrawerItem(null)} /><aside className="evidence-drawer" role="dialog" aria-modal="true" aria-label="证据原文详情"><div className="drawer-head"><div><span className="eyebrow">SOURCE REVIEW</span><h2>证据原文与来源</h2></div><button className="icon-button" aria-label="关闭证据详情" onClick={() => setDrawerItem(null)}><Icon name="close" /></button></div><div className="drawer-body"><div className="drawer-status"><span>{drawerItem.display_source} · {drawerItem.association_scope === 'DRUG_ONLY' ? '单药' : '联合方案'}</span></div>{drawerError && <Failure message={drawerError} onRetry={() => setDrawerItem({ ...drawerItem })} />}{!drawerDetail && !drawerError && <p className="muted">正在读取来源原文…</p>}{drawerDetail && <><h3>{drawerDetail.source_title || `${drawerDetail.display_source} 来源资料`}</h3><dl className="source-facts"><div><dt>来源版本</dt><dd>{drawerDetail.source_version || '来源未提供'}</dd></div><div><dt>项目等级</dt><dd>{drawerDetail.internal_level !== null ? `Level ${drawerDetail.internal_level} · ${drawerDetail.evidence_grade ?? '未定'}` : '未核定'}</dd></div><div><dt>原生推荐等级</dt><dd>{drawerDetail.source_recommendation_raw || '原文未列'}</dd></div><div><dt>原生证据类别</dt><dd>{drawerDetail.source_evidence_category_raw || '原文未列'}</dd></div><div><dt>映射版本</dt><dd>{drawerDetail.grade_mapping_version || '待配置'}</dd></div></dl><div className="quote-heading">原文摘录</div><blockquote>{drawerDetail.verbatim_excerpt || '未保存原文摘录'}</blockquote><div className="quote-heading">原文位置</div><pre className="locator">{JSON.stringify(drawerDetail.source_locator, null, 2)}</pre>{drawerDetail.source_url && <a className="source-link" href={drawerDetail.source_url} target="_blank" rel="noreferrer">打开来源地址 <Icon name="chevron" size={15} /></a>}<p className="drawer-note">此处显示来源记录及当前项目配置，均未代替医学审核。</p></>}</div></aside></div>}
  </>
}
