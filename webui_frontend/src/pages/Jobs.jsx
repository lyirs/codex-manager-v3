// pages/Jobs.jsx
import { useState, useEffect, useRef } from 'react'
import api from '../lib/api.js'
import { StatusBadge, Spinner } from '../components/Badge.jsx'

function elapsed(started) {
  const s = Math.floor(Date.now() / 1000 - started)
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}m ${s % 60}s`
}

export function Jobs() {
  const [jobs, setJobs]               = useState([])
  const [selected, setSelected]       = useState(null)
  const [detail, setDetail]           = useState(null)
  const [form, setForm]               = useState({ count: 1, engine: 'camoufox', provider: 'imap:0' })
  const [starting, setStarting]       = useState(false)
  const [startErr, setStartErr]       = useState('')
  const logRef                        = useRef(null)

  // Poll jobs list
  useEffect(() => {
    const poll = () => api.getJobs().then(setJobs).catch(() => {})
    poll()
    const id = setInterval(poll, 3000)
    return () => clearInterval(id)
  }, [])

  // Poll selected job detail
  useEffect(() => {
    if (!selected) { setDetail(null); return }
    const poll = () =>
      api.getJob(selected).then(d => {
        setDetail(d)
        if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
      }).catch(() => {})
    poll()
    const id = setInterval(poll, 2000)
    return () => clearInterval(id)
  }, [selected])

  const startJob = async () => {
    setStarting(true)
    setStartErr('')
    try {
      const { job_id } = await api.startJob(form)
      setSelected(job_id)
      api.getJobs().then(setJobs)
    } catch (e) {
      setStartErr(e.message)
    } finally {
      setStarting(false)
    }
  }

  const deleteJob = async (id, e) => {
    e.stopPropagation()
    await api.deleteJob(id).catch(() => {})
    if (selected === id) { setSelected(null); setDetail(null) }
    api.getJobs().then(setJobs)
  }

  return (
    <div className="p-6 space-y-6">
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
              <input type="number" min={1} max={100}
                value={form.count}
                onChange={e => setForm(f => ({ ...f, count: +e.target.value }))}
                className="mt-1 block w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              />
            </label>
            <label className="block">
              <span className="text-xs text-gray-500 font-medium">浏览器引擎</span>
              <select
                value={form.engine}
                onChange={e => setForm(f => ({ ...f, engine: e.target.value }))}
                className="mt-1 block w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              >
                <option value="camoufox">Camoufox (推荐)</option>
                <option value="playwright">Playwright</option>
              </select>
            </label>
            <label className="block">
              <span className="text-xs text-gray-500 font-medium">邮件服务</span>
              <select
                value={form.provider}
                onChange={e => setForm(f => ({ ...f, provider: e.target.value }))}
                className="mt-1 block w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              >
                <option value="imap:0">IMAP 账户 1</option>
                <option value="imap:1">IMAP 账户 2</option>
                <option value="gptmail">GptMail</option>
                <option value="npcmail">NpcMail</option>
                <option value="yydsmail">YYDSMail</option>
              </select>
            </label>
            {startErr && <p className="text-xs text-red-500">{startErr}</p>}
            <button
              onClick={startJob}
              disabled={starting}
              className="w-full flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white font-medium py-2.5 rounded-lg text-sm transition-colors"
            >
              {starting ? <Spinner /> : '🚀'} {starting ? '启动中…' : '开始注册'}
            </button>
          </div>
        </div>

        {/* Job list */}
        <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden xl:col-span-2">
          <div className="px-5 py-4 border-b border-gray-100">
            <h3 className="font-semibold text-gray-700">任务列表</h3>
          </div>
          {jobs.length === 0 ? (
            <p className="text-sm text-gray-400 text-center py-12">暂无任务</p>
          ) : (
            <div className="divide-y divide-gray-50">
              {jobs.map(j => (
                <div
                  key={j.id}
                  onClick={() => setSelected(j.id === selected ? null : j.id)}
                  className={`flex items-center justify-between px-5 py-3.5 cursor-pointer transition-colors ${
                    j.id === selected ? 'bg-blue-50' : 'hover:bg-gray-50'
                  }`}
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-xs text-gray-700 font-semibold">{j.id}</span>
                      <StatusBadge status={j.status} />
                    </div>
                    <p className="text-xs text-gray-400 mt-0.5">
                      {j.provider} · ×{j.count} · 成功 {j.success}/{j.done}
                    </p>
                  </div>
                  <div className="flex items-center gap-2 flex-shrink-0 ml-4">
                    {j.status === 'running' && <Spinner />}
                    <span className="text-xs text-gray-400">{elapsed(j.started)}</span>
                    <button
                      onClick={e => deleteJob(j.id, e)}
                      className="text-gray-300 hover:text-red-500 transition-colors text-base"
                      title="删除"
                    >×</button>
                  </div>
                </div>
              ))}
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
              </div>
            )}
          </div>
          <div
            ref={logRef}
            className="log-terminal bg-gray-950 text-green-400 p-4 h-64 overflow-y-auto"
          >
            {detail?.logs?.length ? (
              detail.logs.map((line, i) => <div key={i}>{line}</div>)
            ) : (
              <span className="text-gray-600">等待日志…</span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

