// pages/Settings.jsx
import { useState, useEffect, useCallback } from 'react'
import api from '../lib/api.js'
import { Spinner } from '../components/Badge.jsx'

// ── Shared helpers ────────────────────────────────────────────────────────

function Section({ title, children }) {
  return (
    <div className="border border-gray-100 rounded-xl overflow-hidden">
      <div className="bg-gray-50 px-5 py-3 border-b border-gray-100">
        <h4 className="text-sm font-semibold text-gray-700">{title}</h4>
      </div>
      <div className="p-5">{children}</div>
    </div>
  )
}

function Field({ label, hint, children }) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 items-start py-2.5 border-b border-gray-50 last:border-0">
      <div className="sm:pt-2">
        <p className="text-sm font-medium text-gray-700">{label}</p>
        {hint && <p className="text-xs text-gray-400 mt-0.5">{hint}</p>}
      </div>
      <div className="sm:col-span-2">{children}</div>
    </div>
  )
}

function Input({ type = 'text', ...props }) {
  return (
    <input
      type={type}
      className="block w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
      {...props}
    />
  )
}

function Select({ options, ...props }) {
  return (
    <select
      className="block w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
      {...props}
    >
      {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
    </select>
  )
}

function Toggle({ checked, onChange }) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${checked ? 'bg-blue-600' : 'bg-gray-200'}`}
    >
      <span className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${checked ? 'translate-x-6' : 'translate-x-1'}`} />
    </button>
  )
}

function SaveBtn({ onClick, saving, saved, error }) {
  return (
    <div className="flex items-center gap-3 pt-4">
      <button
        onClick={onClick}
        disabled={saving}
        className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white text-sm font-medium px-5 py-2 rounded-lg transition-colors"
      >
        {saving && <Spinner />}
        {saving ? '保存中…' : '保存'}
      </button>
      {saved && <span className="text-sm text-green-600">✓ 已保存</span>}
      {error && <span className="text-sm text-red-500">{error}</span>}
    </div>
  )
}

function useSave() {
  const [saving, setSaving] = useState(false)
  const [saved, setSaved]   = useState(false)
  const [error, setError]   = useState('')

  const run = async (fn) => {
    setSaving(true); setSaved(false); setError('')
    try {
      await fn()
      setSaved(true)
      setTimeout(() => setSaved(false), 2500)
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }
  return { saving, saved, error, run }
}

// ── Tab: General (YAML-backed) ─────────────────────────────────────────────

function TabGeneral() {
  const [cfg, setCfg] = useState(null)
  const { saving, saved, error, run } = useSave()

  useEffect(() => { api.getConfig().then(setCfg).catch(() => {}) }, [])

  const set = (k, v) => setCfg(c => ({ ...c, [k]: v }))

  const save = () => run(async () => {
    await api.saveConfig({
      engine:          cfg.engine,
      headless:        cfg.headless,
      mobile:          cfg.mobile,
      max_concurrent:  cfg.max_concurrent,
      slow_mo:         cfg.slow_mo,
      mail_provider:   cfg.mail_provider,
      proxy_strategy:  cfg.proxy_strategy,
      proxy_static:    cfg.proxy_static ?? '',
    })
  })

  if (!cfg) return <div className="py-8 text-center text-gray-400"><Spinner size="md" /></div>

  return (
    <div className="space-y-4">
      <Section title="浏览器配置">
        <Field label="引擎" hint="camoufox 可绕过 Turnstile">
          <Select value={cfg.engine} onChange={e => set('engine', e.target.value)}
            options={[['camoufox','Camoufox (Firefox 防指纹，推荐)'],['playwright','Playwright (Chromium)']]} />
        </Field>
        <Field label="无头模式" hint="true = 后台无界面批量运行">
          <Toggle checked={!!cfg.headless} onChange={v => set('headless', v)} />
        </Field>
        <Field label="手机模式" hint="使用手机端指纹">
          <Toggle checked={!!cfg.mobile} onChange={v => set('mobile', v)} />
        </Field>
        <Field label="慢速延迟 (ms)" hint="操作间延迟，0 = 自动">
          <Input type="number" min={0} max={5000} value={cfg.slow_mo ?? 0}
            onChange={e => set('slow_mo', +e.target.value)} />
        </Field>
      </Section>

      <Section title="并发 & 邮件">
        <Field label="最大并发数" hint="同时运行的浏览器数量">
          <Input type="number" min={1} max={20} value={cfg.max_concurrent ?? 2}
            onChange={e => set('max_concurrent', +e.target.value)} />
        </Field>
        <Field label="默认邮件服务商">
          <Select value={cfg.mail_provider ?? 'imap:0'} onChange={e => set('mail_provider', e.target.value)}
            options={[
              ['imap:0','IMAP 账户 1'],['imap:1','IMAP 账户 2'],['imap:2','IMAP 账户 3'],
              ['gptmail','GptMail'],['npcmail','NpcMail'],['yydsmail','YYDSMail'],
            ]} />
        </Field>
      </Section>

      <Section title="代理配置">
        <Field label="代理策略">
          <Select value={cfg.proxy_strategy ?? 'none'} onChange={e => set('proxy_strategy', e.target.value)}
            options={[['none','不使用代理'],['static','固定代理'],['pool','代理池']]} />
        </Field>
        {cfg.proxy_strategy === 'static' && (
          <Field label="固定代理地址" hint="例: http://127.0.0.1:7890">
            <Input value={cfg.proxy_static ?? ''} onChange={e => set('proxy_static', e.target.value)}
              placeholder="http://host:port" />
          </Field>
        )}
      </Section>

      <SaveBtn onClick={save} saving={saving} saved={saved} error={error} />
    </div>
  )
}

// ── Tab: Mail providers (DB-backed) ───────────────────────────────────────

function MailProviderSection({ name, label }) {
  const [data, setData] = useState({ api_key: '', base_url: '' })
  const { saving, saved, error, run } = useSave()

  useEffect(() => {
    api.getSection(`mail.${name}`).then(setData).catch(() => {})
  }, [name])

  const save = () => run(() => api.saveSection(`mail.${name}`, data))

  return (
    <Section title={label}>
      <Field label="API Key">
        <Input type="password" value={data.api_key}
          onChange={e => setData(d => ({ ...d, api_key: e.target.value }))}
          placeholder="sk-..." />
      </Field>
      <Field label="Base URL">
        <Input value={data.base_url}
          onChange={e => setData(d => ({ ...d, base_url: e.target.value }))} />
      </Field>
      <SaveBtn onClick={save} saving={saving} saved={saved} error={error} />
    </Section>
  )
}

function TabMail() {
  return (
    <div className="space-y-4">
      <MailProviderSection name="gptmail" label="GptMail" />
      <MailProviderSection name="npcmail" label="NpcMail" />
      <MailProviderSection name="yydsmail" label="YYDSMail" />
    </div>
  )
}

// ── Tab: IMAP accounts (DB-backed) ────────────────────────────────────────

const EMPTY_IMAP = { email: '', password: '', host: '', port: 993, ssl: true, folder: 'INBOX' }

function TabImap() {
  const [accounts, setAccounts] = useState([])
  const { saving, saved, error, run } = useSave()

  useEffect(() => {
    api.getSection('mail.imap').then(d => setAccounts(Array.isArray(d) ? d : [])).catch(() => {})
  }, [])

  const update = (i, k, v) => setAccounts(a => a.map((acc, idx) => idx === i ? { ...acc, [k]: v } : acc))
  const add    = () => setAccounts(a => [...a, { ...EMPTY_IMAP }])
  const remove = (i) => setAccounts(a => a.filter((_, idx) => idx !== i))
  const save   = () => run(() => api.saveSection('mail.imap', accounts))

  return (
    <div className="space-y-4">
      {accounts.map((acc, i) => (
        <div key={i} className="border border-gray-100 rounded-xl overflow-hidden">
          <div className="bg-gray-50 px-5 py-3 border-b border-gray-100 flex items-center justify-between">
            <h4 className="text-sm font-semibold text-gray-700">IMAP 账户 {i + 1}</h4>
            <button onClick={() => remove(i)}
              className="text-xs text-red-400 hover:text-red-600 transition-colors">删除</button>
          </div>
          <div className="p-5 space-y-0">
            <Field label="邮箱地址">
              <Input value={acc.email} onChange={e => update(i, 'email', e.target.value)}
                placeholder="user@qq.com" />
            </Field>
            <Field label="密码 / 授权码">
              <Input type="password" value={acc.password} onChange={e => update(i, 'password', e.target.value)} />
            </Field>
            <Field label="IMAP 服务器">
              <Input value={acc.host} onChange={e => update(i, 'host', e.target.value)}
                placeholder="imap.qq.com" />
            </Field>
            <Field label="端口">
              <Input type="number" value={acc.port} onChange={e => update(i, 'port', +e.target.value)} />
            </Field>
            <Field label="SSL/TLS" hint="993=IMAPS(推荐), 143=STARTTLS">
              <Toggle checked={!!acc.ssl} onChange={v => update(i, 'ssl', v)} />
            </Field>
            <Field label="收件箱文件夹">
              <Input value={acc.folder} onChange={e => update(i, 'folder', e.target.value)} />
            </Field>
          </div>
        </div>
      ))}

      <button onClick={add}
        className="w-full border-2 border-dashed border-gray-200 text-gray-400 hover:border-blue-300 hover:text-blue-500 rounded-xl py-3 text-sm transition-colors">
        + 添加 IMAP 账户
      </button>

      <SaveBtn onClick={save} saving={saving} saved={saved} error={error} />
    </div>
  )
}

// ── Tab: Timeouts (DB-backed) ─────────────────────────────────────────────

const TIMEOUT_LABELS = {
  page_load:            ['页面加载', 'page.goto() 超时'],
  auth0_redirect:       ['Auth0 重定向', '等待跳转到 auth.openai.com'],
  email_input:          ['邮箱输入框', '等待邮箱输入框出现'],
  password_input:       ['密码输入框', '等待密码输入框出现'],
  otp_input:            ['OTP 输入框', '等待 OTP 输入框出现'],
  otp_code:             ['OTP 验证码', '轮询邮箱获取验证码'],
  profile_detect:       ['姓名页检测', '等待姓名输入框出现'],
  profile_field:        ['姓名字段', '等待每个字段'],
  complete_redirect:    ['注册完成跳转', '等待跳回 chatgpt.com'],
  oauth_navigate:       ['OAuth 导航', 'page.goto() to /oauth/authorize'],
  oauth_flow_element:   ['OAuth 元素', '等待授权按钮出现'],
  oauth_login_email:    ['OAuth 邮箱', '等待 OAuth 重新登录邮箱框'],
  oauth_login_password: ['OAuth 密码', '等待 OAuth 重新登录密码框'],
  oauth_token_exchange: ['Token 交换', 'httpx /oauth/token 超时'],
  oauth_total:          ['OAuth 总超时', 'OAuth 流程硬超时'],
}

function TabTimeouts() {
  const [data, setData] = useState({})
  const { saving, saved, error, run } = useSave()

  useEffect(() => { api.getSection('timeouts').then(setData).catch(() => {}) }, [])

  const set = (k, v) => setData(d => ({ ...d, [k]: v }))
  const save = () => run(() => api.saveSection('timeouts', data))

  return (
    <div className="space-y-4">
      <Section title="超时配置（单位：秒）">
        {Object.entries(TIMEOUT_LABELS).map(([k, [label, hint]]) => (
          <Field key={k} label={label} hint={hint}>
            <Input type="number" min={1} max={600}
              value={data[k] ?? ''}
              onChange={e => set(k, +e.target.value)} />
          </Field>
        ))}
      </Section>
      <SaveBtn onClick={save} saving={saving} saved={saved} error={error} />
    </div>
  )
}

// ── Tab: Advanced (DB-backed) ─────────────────────────────────────────────

function TabAdvanced() {
  const [mouse,   setMouse]   = useState({})
  const [timing,  setTiming]  = useState({})
  const [oauth,   setOauth]   = useState({ enabled: true, timeout: 45 })
  const [reg,     setReg]     = useState({ prefix: '', domain: '' })
  const [team,    setTeam]    = useState({ url: '', key: '' })
  const [sync,    setSync]    = useState({ url: '', key: '' })

  const { saving, saved, error, run } = useSave()

  useEffect(() => {
    Promise.all([
      api.getSection('mouse').then(setMouse),
      api.getSection('timing').then(setTiming),
      api.getSection('oauth').then(setOauth),
      api.getSection('registration').then(setReg),
      api.getSection('team').then(setTeam),
      api.getSection('sync').then(setSync),
    ]).catch(() => {})
  }, [])

  const save = () => run(async () => {
    await Promise.all([
      api.saveSection('mouse', mouse),
      api.saveSection('timing', timing),
      api.saveSection('oauth', oauth),
      api.saveSection('registration', reg),
      api.saveSection('team', team),
      api.saveSection('sync', sync),
    ])
  })

  const mf = (k, v) => setMouse(d => ({ ...d, [k]: v }))
  const tf = (k, v) => setTiming(d => ({ ...d, [k]: v }))

  return (
    <div className="space-y-4">
      <Section title="鼠标模拟配置">
        {[
          ['steps_min',       '最少弧线步数', ''],
          ['steps_max',       '最多弧线步数', ''],
          ['step_delay_min',  '每步最短延迟 (秒)', ''],
          ['step_delay_max',  '每步最长延迟 (秒)', ''],
          ['hover_min',       '悬停最短时间 (秒)', ''],
          ['hover_max',       '悬停最长时间 (秒)', ''],
        ].map(([k, label, hint]) => (
          <Field key={k} label={label} hint={hint}>
            <Input type="number" step="0.001" min={0} value={mouse[k] ?? ''}
              onChange={e => mf(k, +e.target.value)} />
          </Field>
        ))}
      </Section>

      <Section title="等待时间配置 (秒)">
        {[
          ['post_nav',      '导航后等待',   '跳转/重定向后等待时间'],
          ['pre_fill',      '填写前等待',   '填写/点击前等待时间'],
          ['post_click',    '点击后等待',   '点击提交按钮后等待时间'],
          ['post_complete', '完成后等待',   'COMPLETE 状态结束前等待'],
        ].map(([k, label, hint]) => (
          <Field key={k} label={label} hint={hint}>
            <Input type="number" step="0.1" min={0} value={timing[k] ?? ''}
              onChange={e => tf(k, +e.target.value)} />
          </Field>
        ))}
      </Section>

      <Section title="OAuth 配置">
        <Field label="自动获取 Token" hint="注册完成后自动完成 OAuth 授权">
          <Toggle checked={!!oauth.enabled} onChange={v => setOauth(d => ({ ...d, enabled: v }))} />
        </Field>
        <Field label="OAuth 超时 (秒)">
          <Input type="number" min={10} max={300} value={oauth.timeout ?? 45}
            onChange={e => setOauth(d => ({ ...d, timeout: +e.target.value }))} />
        </Field>
      </Section>

      <Section title="注册配置">
        <Field label="邮箱前缀" hint="生成邮箱地址时的前缀">
          <Input value={reg.prefix ?? ''} onChange={e => setReg(d => ({ ...d, prefix: e.target.value }))} />
        </Field>
        <Field label="邮箱域名" hint="留空则使用邮件服务商默认域名">
          <Input value={reg.domain ?? ''} onChange={e => setReg(d => ({ ...d, domain: e.target.value }))} />
        </Field>
      </Section>

      <Section title="团队 & 同步">
        <Field label="Team URL">
          <Input value={team.url ?? ''} onChange={e => setTeam(d => ({ ...d, url: e.target.value }))}
            placeholder="https://..." />
        </Field>
        <Field label="Team Key">
          <Input type="password" value={team.key ?? ''} onChange={e => setTeam(d => ({ ...d, key: e.target.value }))} />
        </Field>
        <Field label="Sync URL">
          <Input value={sync.url ?? ''} onChange={e => setSync(d => ({ ...d, url: e.target.value }))}
            placeholder="https://..." />
        </Field>
        <Field label="Sync Key">
          <Input type="password" value={sync.key ?? ''} onChange={e => setSync(d => ({ ...d, key: e.target.value }))} />
        </Field>
      </Section>

      <SaveBtn onClick={save} saving={saving} saved={saved} error={error} />
    </div>
  )
}

// ── Main Settings page ─────────────────────────────────────────────────────

const TABS = [
  { key: 'general',   label: '⚙️ 通用配置' },
  { key: 'mail',      label: '📧 邮件服务' },
  { key: 'imap',      label: '📥 IMAP 账户' },
  { key: 'timeouts',  label: '⏱ 超时设置' },
  { key: 'advanced',  label: '🔧 高级设置' },
]

export function Settings() {
  const [tab, setTab] = useState('general')

  return (
    <div className="p-6 space-y-5">
      <div>
        <h2 className="text-xl font-bold text-gray-800">配置管理</h2>
        <p className="text-sm text-gray-500 mt-0.5">通用配置保存至 config.yaml，其余设置存入数据库</p>
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-gray-200">
        {TABS.map(t => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-4 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors whitespace-nowrap ${
              tab === t.key
                ? 'border-blue-600 text-blue-600'
                : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div>
        {tab === 'general'  && <TabGeneral />}
        {tab === 'mail'     && <TabMail />}
        {tab === 'imap'     && <TabImap />}
        {tab === 'timeouts' && <TabTimeouts />}
        {tab === 'advanced' && <TabAdvanced />}
      </div>
    </div>
  )
}

