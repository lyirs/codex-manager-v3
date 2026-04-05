// src/lib/api.js — Thin wrapper around fetch for the FastAPI backend.
const BASE = '/api'

async function req(method, path, body) {
  const opts = { method }
  if (body !== undefined) {
    opts.headers = { 'Content-Type': 'application/json' }
    opts.body = JSON.stringify(body)
  }
  const res = await fetch(BASE + path, opts)
  if (!res.ok) {
    let msg = `${res.status}`
    try { msg = (await res.json()).detail || msg } catch { /* ignore */ }
    throw new Error(msg)
  }
  return res.json()
}

const api = {
  // ── Common config (YAML-backed) ─────────────────────────────────────
  getConfig:  ()       => req('GET',  '/config'),
  saveConfig: (data)   => req('POST', '/config', data),

  // ── Non-common settings (DB-backed) ─────────────────────────────────
  getSettings:        ()            => req('GET',  '/settings'),
  getSection:         (s)           => req('GET',  `/settings/${encodeURIComponent(s)}`),
  saveSection:        (s, data)     => req('POST', `/settings/${encodeURIComponent(s)}`, data),
  getMergedConfig:    ()            => req('GET',  '/settings_merged'),

  // ── Accounts ─────────────────────────────────────────────────────────
  getAccounts: (params = {}) => req('GET', '/accounts?' + new URLSearchParams(params)),
  getStats:    ()            => req('GET', '/accounts/stats'),
  exportUrl:   (fmt)         => `${BASE}/accounts/export?fmt=${fmt}`,

  // ── Jobs ─────────────────────────────────────────────────────────────
  getJobs:   ()     => req('GET',    '/jobs'),
  getJob:    (id)   => req('GET',    `/jobs/${id}`),
  startJob:  (data) => req('POST',   '/jobs', data),
  deleteJob: (id)   => req('DELETE', `/jobs/${id}`),

  // ── Proxies ──────────────────────────────────────────────────────────
  getProxies:   ()    => req('GET',    '/proxies'),
  addProxy:     (addr)=> req('POST',   '/proxies', { address: addr }),
  deleteProxy:  (addr)=> req('DELETE', `/proxies/${encodeURIComponent(addr)}`),
}

export default api

