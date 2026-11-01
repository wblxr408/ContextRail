import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'

type Config = { revision: string; workspace: string; task: string; source: string; clients: { client: string; enabled: boolean }[] }

/** Local adapter: preserve the existing registry and change only a project switch. */
export function MyRules() {
  const [config, setConfig] = useState<Config | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [saving, setSaving] = useState(false)
  async function load() {
    const response = await fetch('/api/rail/config')
    const data = await response.json()
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`)
    setConfig(data); setError('')
  }
  useEffect(() => {
    const controller = new AbortController()
    fetch('/api/rail/config', { signal: controller.signal }).then(async response => {
      const data = await response.json()
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`)
      setConfig(data)
    }).catch(error => { if (!controller.signal.aborted) setError(String(error)) })
    return () => controller.abort()
  }, [])
  async function save(client: string, enabled: boolean) {
    if (!config) return
    setSaving(true); setNotice(''); setError('')
    try {
      const response = await fetch('/api/rail/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ client, enabled, expectedRevision: config.revision }) })
      const data = await response.json()
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`)
      setConfig(data); setNotice(`${client} 已${enabled ? '启用' : '暂停'}；下次原生 Hook 生效。`)
    } catch (error) { setError(String(error)) }
    finally { setSaving(false) }
  }
  return <section className="mx-auto max-w-3xl space-y-6 p-6">
    <div><h1 className="text-2xl font-semibold">项目接线设置</h1><p className="mt-2 text-sm text-muted-foreground">沿用现有 Codex / Claude Code Hook 和记忆服务。这里管理当前项目的接入开关。</p></div>
    {error && <div role="alert" className="rounded-xl border p-4 text-sm">{error} <Button variant="outline" size="sm" onClick={() => void load().catch(error => setError(String(error)))}>重新读取</Button></div>}
    {notice && <p role="status" className="text-sm">{notice}</p>}
    {config && <><div className="space-y-3 rounded-xl border bg-card p-5"><p className="break-all text-sm">{config.workspace}</p><p className="text-xs text-muted-foreground">任务 {config.task}</p>{config.clients.map(client => <div key={client.client} className="flex items-center justify-between gap-4 border-t pt-3"><span className="text-sm">{client.client} · {client.enabled ? '已启用' : '已暂停'}</span><Button disabled={saving} variant="outline" size="sm" onClick={() => void save(client.client, !client.enabled)}>{client.enabled ? '暂停' : '启用'} {client.client}</Button></div>)}</div><p className="break-all text-xs text-muted-foreground">保存到 {config.source}。MCP 工具目录由客户端重载；界面不会修改账号或模型。</p></>}
    <div className="flex flex-wrap gap-4 text-sm"><a href="/rail" className="underline">故障解释、文件历史与交付物</a><a href="/viewer" className="underline">打开原 main 和记忆时间线</a></div>
  </section>
}
