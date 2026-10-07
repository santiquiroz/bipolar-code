import { useState } from 'react'
import { Card } from '@/components/Card'
import { Button } from '@/components/Button'
import { Badge } from '@/components/Badge'
import { useAccounts, useCreateAccount, useDeleteAccount } from '@/hooks/useAccounts'
import type { AccountInfo, LoginCommands } from '@/types/smart'

const field = 'border border-gray-300 rounded-lg px-2 py-1.5 text-xs'
const bases = ['claude', 'codex', 'deepseek']
const backIn = (s: number) => `vuelve en ${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`
const loginBadge = (a: AccountInfo) => a.has_login === null ? <Badge label="Login desconocido" variant="info" /> : a.has_login ? <Badge label="Con login" variant="success" /> : <Badge label="Sin login" variant="warning" />
const stateVariant = (s: string) => s === 'available' ? 'success' as const : ['exhausted', 'cooling'].includes(s) ? 'warning' as const : 'neutral' as const

function UsageBars({ account }: { account: AccountInfo }) {
  if (!account.usage?.five_hour && !account.usage?.seven_day) return <p className="text-xs text-gray-400 mt-1">sin datos de uso</p>
  const wins = [['5 h', account.usage.five_hour], ['7 días', account.usage.seven_day]] as const
  return <div className="space-y-1 mt-1">{wins.map(([name, w]) => w == null ? null : <div key={name} className="flex items-center gap-2 text-xs text-gray-600"><span className="w-24 shrink-0">{name}: {Math.round(w.used_percentage)} %</span><div className="flex-1 h-2 bg-gray-100 rounded-full"><div className="h-2 bg-brand-600 rounded-full" style={{ width: `${Math.min(100, w.used_percentage)}%` }} /></div></div>)}</div>
}

function AccountRow({ account, onDelete, deleting }: { account: AccountInfo; onDelete: (id: string) => void; deleting: boolean }) {
  const [confirming, setConfirming] = useState(false)
  return <div className="border-b border-gray-50 py-2">
    <div className="flex items-center justify-between gap-2"><div className="text-xs font-medium text-gray-800">{account.label} <span className="font-normal text-gray-400">({account.agent_id}) · {account.base}</span></div>{confirming ? <div className="flex gap-1"><Button size="sm" variant="danger" loading={deleting} onClick={() => onDelete(account.agent_id)}>Confirmar</Button><Button size="sm" variant="secondary" onClick={() => setConfirming(false)}>Cancelar</Button></div> : <Button size="sm" variant="secondary" onClick={() => setConfirming(true)}>Quitar</Button>}</div>
    <div className="flex flex-wrap items-center gap-1 mt-1">{loginBadge(account)}<Badge label={account.state} variant={stateVariant(account.state)} />{account.seconds_left > 0 && <span className="text-xs text-gray-500">{backIn(account.seconds_left)}</span>}</div>
    <UsageBars account={account} />
  </div>
}

function LoginPanel({ login, onDone }: { login: LoginCommands; onDone: () => void }) {
  const cmds = [['PowerShell', login.powershell], ['Bash', login.bash]] as const
  return <div className="mt-3 border border-gray-200 rounded-lg p-3 space-y-2">
    {cmds.map(([name, cmd]) => <div key={name}><div className="flex justify-between items-center"><span className="text-xs text-gray-500">{name}</span><Button size="sm" variant="secondary" onClick={() => navigator.clipboard.writeText(cmd)}>Copiar</Button></div><pre className="text-xs bg-gray-50 rounded p-2 mt-1 whitespace-pre-wrap break-all">{cmd}</pre></div>)}
    {login.note && <p className="text-xs text-gray-600">{login.note}</p>}
    <p className="text-xs text-gray-500">Si es una cuenta de Claude, abre Claude Code con ese comando y ejecuta /login con la cuenta que quieras asociar.</p>
    <Button size="sm" onClick={onDone}>Listo</Button>
  </div>
}

export function AccountsCard() {
  const { data } = useAccounts()
  const create = useCreateAccount()
  const del = useDeleteAccount()
  const [base, setBase] = useState('claude')
  const [label, setLabel] = useState('')
  const [login, setLogin] = useState<LoginCommands | null>(null)
  const submit = () => create.mutate({ base, label }, { onSuccess: res => { setLogin(res.login); setLabel('') } })
  return <Card title="Cuentas">
    {(data?.accounts || []).map(a => <AccountRow key={a.agent_id} account={a} deleting={del.isPending} onDelete={id => del.mutate(id)} />)}
    <p className="text-xs font-medium text-gray-700 mt-3">Agregar cuenta</p>
    <div className="flex flex-wrap items-center gap-2 mt-1"><label className="text-xs text-gray-500">Base<select className={`${field} ml-1`} value={base} onChange={e => setBase(e.target.value)}>{bases.map(b => <option key={b}>{b}</option>)}</select></label><label className="text-xs text-gray-500">Etiqueta<input className={`${field} ml-1`} value={label} onChange={e => setLabel(e.target.value)} /></label><Button size="sm" loading={create.isPending} onClick={submit}>Crear</Button></div>
    {login && <LoginPanel login={login} onDone={() => setLogin(null)} />}
  </Card>
}
