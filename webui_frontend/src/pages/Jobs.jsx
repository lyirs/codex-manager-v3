// pages/Jobs.jsx
import { useState, useEffect, useRef } from 'react'
import api from '../lib/api.js'
import { StatusBadge, Spinner } from '../components/Badge.jsx'

function elapsed(started) {
  const s = Math.floor(Date.now() / 1000 - started)
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}m ${s % 60}s`
}

function useProviderOptions() {
  const [opts, setOpts] = useState([['imap:0', 'IMAP 服务商 1'], ['gptmail', 'GptMail']])
  useEffect(() => {
    api.getSettings().then(s => {
      const items = []
      const imapProviders = Array.isArray(s['mail.imap']) ? s['mail.imap'] : []
      const isNewFormat = imapProviders.length > 0 && 'accounts' in imapProviders[0]

      if (isNewFormat) {
        imapProviders.forEach((prov, i) => {
          const name  = prov.name || `IMAP 服务商 ${i + 1}`
          const accs  = Array.isArray(prov.accounts) ? prov.accounts : []
          // Group-level: rotate through all accounts in this provider
          items.push([`imap:${i}`, `${name}（全部 ${accs.length} 账户轮换）`])
          // Individual accounts within this provider
          accs.forEach((acc, j) => {
            const label = acc.email ? acc.email : `账户 ${j + 1}`
            items.push([`imap:${i}:${j}`, `└ ${label}`])
          })
        })
      } else {
        imapProviders.forEach((acc, i) =>
          items.push([`imap:${i}`, acc.email ? `IMAP: ${acc.email}` : `IMAP 账户 ${i + 1}`])
        )
      }

      if (items.filter(([v]) => v.startsWith('imap')).length === 0)
        items.push(['imap:0', 'IMAP 服务商 1'])

      const outlookAccounts = Array.isArray(s['mail.outlook']) ? s['mail.outlook'] : []
      if (outlookAccounts.length > 0) {
        // Group-level: rotate through all Outlook accounts
        items.push(['outlook', `Outlook（全部 ${outlookAccounts.length} 账户轮换）`])
        // Individual Outlook accounts
        outlookAccounts.forEach((acc, i) => {
          const label = acc.email ? acc.email : `Outlook 账户 ${i + 1}`
          items.push([`outlook:${i}`, `└ ${label}`])
        })
      }

      items.push(['gptmail', 'GptMail'], ['npcmail', 'NpcMail'], ['yydsmail', 'YYDSMail'])
      setOpts(items)
    }).catch(() => {})
  }, [])
  return opts
}

function IndeterminateCheckbox({ indeterminate, ...props }) {
  const ref = useRef(null)
  useEffect(() => { if (ref.current) ref.current.indeterminate = !!indeterminate }, [indeterminate])
  return <input type="checkbox" ref={ref} className="rounded cursor-pointer accent-blue-600" {...props} />
}

function BulkBar({ selCount, total, selAll, onSelectAll, onClearSel, children }) {
  if (selCount === 0 && !selAll) return null
  const displayCount = selAll ? total : selCount
  return (
    <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex items-center gap-3 bg-gray-900 text-white rounded-2xl shadow-2xl px-5 py-3 text-sm whitespace-nowrap">
      <span className="font-medium">
        已选 <span className="text-blue-400 font-bold">{displayCount}</span> 个任务
        {selAll && <span className="ml-1 text-xs text-green-400 font-semibold">（全部）</span>}
      </span>
      {!selAll && selCount > 0 && total > selCount && (
        <button onClick={onSelectAll} className="text-xs text-blue-400 hover:text-blue-300 underline">
          选择全部 {total} 个
        </button>
      )}
      <div className="w-px h-4 bg-gray-700 flex-shrink-0" />
      {children}
      <button onClick={onClearSel} className="text-gray-500 hover:text-white text-lg leading-none ml-1">×</button>
    </div>
  )
}

export function Jobs() {
  const [jobs, setJobs]           = useState([])
  const [selected, setSelected]   = useState(null)
  const [detail, setDetail]       = useState(null)
  const [form, setForm]           = useState({ count: 1, engine: 'camoufox', provider: 'imap:0' })
  const [starting, setStarting]   = useState(false)
  const [startErr, setStartErr]   = useState('')
  const logRef                    = useRef(null)
  const providerOpts              = useProviderOptions()

  // Bulk selection
  const [sel, setSel]       = useState(new Set())
  const [selAll, setSelAll] = useState(false)
  const [batchBusy, setBatchBusy] = useState(false)

  useEffect(() => {
    const poll = () => api.getJobs().then(setJobs).catch(() => {})
    poll()
    const id = setInterval(poll, 3000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    api.getConfig().then(cfg => {
      setForm(f => ({
        ...f,
        engine: cfg?.engine || f.engine,
        provider: cfg?.mail_provider || f.provider,
      }))
    }).catch(() => {})
  }, [])

  useEffect(() => {
    if (!selected) { setDetail(null); return }
    const poll = () => api.getJob(selected).then(d => { setDetail(d); if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight }).catch(() => {})
    poll()
    const id = setInterval(poll, 2000)
    return () => clearInterval(id)
  }, [selected])

  const startJob = async () => {
    setStarting(true); setStartErr('')
    try { const { job_id } = await api.startJob(form); setSelected(job_id); api.getJobs().then(setJobs) }
    catch (e) { setStartErr(e.message) }
    finally { setStarting(false) }
  }

  const cancelJob = async (id, e) => { e.stopPropagation(); await api.cancelJob(id).catch(() => {}); api.getJobs().then(setJobs) }
  const deleteJob = async (id, e) => {
    e.stopPropagation()
    await api.deleteJob(id).catch(() => {})
    if (selected === id) { setSelected(null); setDetail(null) }
    api.getJobs().then(setJobs)
  }

  // Bulk selection helpers
  const allIds  = jobs.map(j => j.id)
  const allSel  = allIds.length > 0 && allIds.every(id => sel.has(id))
  const someSel = !allSel && allIds.some(id => sel.has(id))

  const toggleRow = (id) => { setSelAll(false); setSel(s => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n }) }
  const toggleAll = () => { setSelAll(false); setSel(s => { const n = new Set(s); if (allSel) allIds.forEach(id => n.delete(id)); else allIds.forEach(id => n.add(id)); return n }) }
  const clearSel  = () => { setSel(new Set()); setSelAll(false) }

  const handleBatchAction = async (action) => {
    const n = selAll ? jobs.length : sel.size
    const label = action === 'cancel' ? '取消' : '删除'
    if (!window.confirm(`确认${label} ${n} 个任务？`)) return
    setBatchBusy(true)
    try {
      await api.batchJobsAction(selAll ? { action, select_all: true } : { action, ids: [...sel] })
      clearSel()
      api.getJobs().then(setJobs)
      if (action === 'delete' && sel.has(selected)) { setSelected(null); setDetail(null) }
    } catch (e) { alert(`${label}失败：` + e.message) }
    finally { setBatchBusy(false) }
  }

  return (
    <div className="p-6 space-y-6 pb-24">
      <div>
        <h2 className="text-xl font-bold text-gray-800">注册任务</h2>
        <p className="text-sm text-gray-500 mt-0.5">启动并监控批量注册任务</p>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        {/* Start form */}
        <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-5">
          <h3 className="font-semibold text-gray-700 mb-4">新建任务</h3>
          <div className="space-y-3">
            <label className="block">
              <span className="text-xs text-gray-500 font-medium">注册数量</span>
              <input type="number" min={1} max={100} value={form.count}
                onChange={e => setForm(f => ({ ...f, count: +e.target.value }))}
                className="mt-1 block w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400" />
            </label>
            <label className="block">
              <span className="text-xs text-gray-500 font-medium">浏览器引擎</span>
              <select value={form.engine} onChange={e => setForm(f => ({ ...f, engine: e.target.value }))}
                className="mt-1 block w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400">
                <option value="camoufox">Camoufox (推荐)</option>
                <option value="playwright">Playwright</option>
              </select>
            </label>
            <label className="block">
              <span className="text-xs text-gray-500 font-medium">邮件服务</span>
              <select value={form.provider} onChange={e => setForm(f => ({ ...f, provider: e.target.value }))}
                className="mt-1 block w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400">
                {providerOpts.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </label>
            {startErr && <p className="text-xs text-red-500">{startErr}</p>}
            <button onClick={startJob} disabled={starting}
              className="w-full flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white font-medium py-2.5 rounded-lg text-sm transition-colors">
              {starting ? <Spinner /> : '🚀'} {starting ? '启动中…' : '开始注册'}
            </button>
          </div>
        </div>

        {/* Job list */}
        <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden xl:col-span-2">
          <div className="px-5 py-4 border-b border-gray-100 flex items-center gap-3">
            {jobs.length > 0 && (
              <IndeterminateCheckbox
                checked={allSel || selAll}
                indeterminate={someSel && !selAll}
                onChange={toggleAll}
              />
            )}
            <h3 className="font-semibold text-gray-700">任务列表</h3>
          </div>
          {jobs.length === 0 ? (
            <p className="text-sm text-gray-400 text-center py-12">暂无任务</p>
          ) : (
            <div className="divide-y divide-gray-50">
              {jobs.map(j => {
                const checked = selAll || sel.has(j.id)
                return (
                  <div
                    key={j.id}
                    className={`flex items-center gap-3 px-5 py-3.5 transition-colors ${checked ? 'bg-blue-50' : j.id === selected ? 'bg-blue-50' : 'hover:bg-gray-50'}`}
                  >
                    {/* Checkbox (stops click propagation) */}
                    <div onClick={e => { e.stopPropagation(); toggleRow(j.id) }} className="flex-shrink-0">
                      <input type="checkbox" checked={checked} onChange={() => {}} className="rounded cursor-pointer accent-blue-600" />
                    </div>
                    {/* Row content (selects job for log) */}
                    <div className="flex-1 min-w-0 cursor-pointer" onClick={() => setSelected(j.id === selected ? null : j.id)}>
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-xs text-gray-700 font-semibold">{j.id}</span>
                        <StatusBadge status={j.status} />
                      </div>
                      <p className="text-xs text-gray-400 mt-0.5">{j.provider} · ×{j.count} · 成功 {j.success}/{j.done}</p>
                    </div>
                    <div className="flex items-center gap-2 flex-shrink-0 ml-2">
                      {j.status === 'running' && <Spinner />}
                      <span className="text-xs text-gray-400">{elapsed(j.started)}</span>
                      {j.status === 'running' && (
                        <button onClick={e => cancelJob(j.id, e)}
                          className="text-xs text-orange-400 hover:text-orange-600 border border-orange-200 hover:border-orange-400 px-1.5 py-0.5 rounded transition-colors"
                          title="取消任务">取消</button>
                      )}
                      <button onClick={e => deleteJob(j.id, e)}
                        className="text-gray-300 hover:text-red-500 transition-colors text-base" title="删除">×</button>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>

      {/* Log panel */}
      {selected && (
        <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
          <div className="px-5 py-4 border-b border-gray-100 flex items-center justify-between">
            <h3 className="font-semibold text-gray-700">
              任务日志 <span className="font-mono text-blue-500 text-sm">{selected}</span>
            </h3>
            {detail && (
              <div className="flex items-center gap-3 text-xs text-gray-500">
                <span>进度: {detail.done}/{detail.count}</span>
                <span>成功: {detail.success}</span>
                <StatusBadge status={detail.status} />
                {detail.status === 'running' && (
                  <button onClick={e => cancelJob(selected, e)}
                    className="text-xs text-orange-500 hover:text-orange-700 border border-orange-200 hover:border-orange-400 px-2 py-0.5 rounded transition-colors font-medium">
                    ⛔ 取消任务
                  </button>
                )}
              </div>
            )}
          </div>
          <div ref={logRef} className="log-terminal bg-gray-950 text-green-400 p-4 h-64 overflow-y-auto">
            {detail?.logs?.length
              ? detail.logs.map((line, i) => {
                  const isOAuth = line.includes('[OAuth]')
                  const isWarn  = line.includes('⚠️') || line.includes('错误') || line.includes('失败')
                  const isOk    = line.includes('✅') || line.includes('成功')
                  let cls = 'text-green-400'
                  if (isOAuth && isOk)   cls = 'text-blue-400 font-medium'
                  else if (isOAuth && isWarn) cls = 'text-yellow-400'
                  else if (isOAuth)      cls = 'text-cyan-400'
                  else if (isWarn)       cls = 'text-yellow-500'
                  return <div key={i} className={cls}>{line}</div>
                })
              : <span className="text-gray-600">等待日志…</span>
            }
          </div>
        </div>
      )}

      {/* Bulk action bar */}
      <BulkBar selCount={sel.size} total={jobs.length} selAll={selAll} onSelectAll={() => setSelAll(true)} onClearSel={clearSel}>
        <button onClick={() => handleBatchAction('cancel')} disabled={batchBusy}
          className="flex items-center gap-1.5 bg-orange-500 hover:bg-orange-600 disabled:opacity-60 text-white text-xs font-medium px-3 py-1.5 rounded-lg transition-colors">
          {batchBusy ? '处理中…' : '⛔ 取消所选'}
        </button>
        <button onClick={() => handleBatchAction('delete')} disabled={batchBusy}
          className="flex items-center gap-1.5 bg-red-500 hover:bg-red-600 disabled:opacity-60 text-white text-xs font-medium px-3 py-1.5 rounded-lg transition-colors">
          {batchBusy ? '处理中…' : '🗑️ 删除所选'}
        </button>
      </BulkBar>
    </div>
  )
}

