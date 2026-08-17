import React, { useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { Amplify } from 'aws-amplify'
import { events } from 'aws-amplify/data'
import { fetchAuthSession, getCurrentUser } from 'aws-amplify/auth'
import { Authenticator } from '@aws-amplify/ui-react'
import '@aws-amplify/ui-react/styles.css'
import './styles.css'


const DEFAULT_QUESTION = 'Identify the three most important anomalous HDFS log behaviors and give me an action plan.'

function normalizeEvent(value) {
  if (typeof value === 'string') {
    try { return JSON.parse(value) } catch { return { type: 'token', text: value } }
  }
  if (value?.event) return normalizeEvent(value.event)
  if (value?.data) return normalizeEvent(value.data)
  return value || {}
}

function Confidence({ value }) {
  const percent = Math.max(0, Math.min(100, Math.round(Number(value || 0) * 100)))
  return <span className="confidence">{percent}% signal</span>
}

// 269 arrows of "0 -> 2 -> 3" is unreadable, and the structure inside it is the
// whole point of the finding: a long unbroken run looks different from a short
// repeating cycle. Draw one tick per event, coloured per Drain template, so the
// shape the probable cause describes is visible instead of described.
function templateHue(code) {
  const n = Number(code)
  return (Number.isFinite(n) ? n * 47 : String(code).length * 47) % 360
}

function EventSequence({ codes }) {
  const list = Array.isArray(codes) ? codes.map(String) : []
  if (!list.length) return null
  const counts = list.reduce((acc, code) => ({ ...acc, [code]: (acc[code] || 0) + 1 }), {})
  const ranked = Object.entries(counts).sort((a, b) => b[1] - a[1])
  let longestRun = 1
  let run = 1
  for (let i = 1; i < list.length; i += 1) {
    run = list[i] === list[i - 1] ? run + 1 : 1
    if (run > longestRun) longestRun = run
  }
  return (
    <figure className="seq">
      <div
        className="seq-strip"
        role="img"
        aria-label={`${list.length} HDFS events across ${ranked.length} Drain templates`}
      >
        {list.map((code, index) => (
          <i
            key={`${index}-${code}`}
            style={{ background: `hsl(${templateHue(code)} 68% 56%)` }}
            title={`event ${index + 1} of ${list.length} · template ${code}`}
          />
        ))}
      </div>
      <figcaption className="seq-legend">
        {ranked.slice(0, 6).map(([code, n]) => (
          <span key={code}>
            <i style={{ background: `hsl(${templateHue(code)} 68% 56%)` }} />
            {code} <b>{Math.round((100 * n) / list.length)}%</b>
          </span>
        ))}
        <span className="seq-total">
          {list.length} events · {ranked.length} templates · longest run {longestRun}
        </span>
      </figcaption>
    </figure>
  )
}

function FindingCard({ finding }) {
  return (
    <article className="finding-card">
      <header>
        <span className="rank">0{finding.rank}</span>
        <div>
          <p className="eyebrow">Anomalous pattern</p>
          <h3>{finding.finding_id}</h3>
        </div>
        <div className="finding-badges"><span className={`severity ${String(finding.severity || 'medium').toLowerCase()}`}>{finding.severity || 'MEDIUM'}</span><Confidence value={finding.confidence || finding.anomaly_score} /></div>
      </header>
      <EventSequence codes={finding.event_codes} />
      <p className="probable-cause"><b>Probable cause</b>{finding.probable_cause}</p>
      <div className="chips">
        {(finding.event_codes || []).slice(0, 6).map((code) => <span key={code}>{code}</span>)}
        {(finding.affected_blocks || []).slice(0, 3).map((block) => <span key={block}>{block}</span>)}
        {(finding.affected_hosts || []).slice(0, 2).map((host) => <span key={host}>host {String(host).slice(0, 10)}</span>)}
      </div>
      <div className="action-grid">
        {(finding.action_plan || []).map((action, index) => (
          <div className="action" key={action}>
            <b>{index + 1}</b><span>{action}</span>
          </div>
        ))}
      </div>
      <details className="evidence-drawer">
        <summary>Evidence, graph paths, and citations</summary>
        <div className="evidence-grid">
          <div><b>Why ranked</b><ul>{(finding.why_ranked || []).map((reason) => <li key={reason}>{reason}</li>)}</ul></div>
          <div><b>Raw event sequence</b><p className="raw-seq">{finding.summary}</p></div>
          <div><b>Source citations</b><ul>{(finding.citations || []).map((citation, index) => <li key={`${citation.store}-${citation.id || index}`}><code>{citation.store}</code> · {citation.id || citation.pattern_id}{citation.source_uri && <small>{citation.source_uri}</small>}</li>)}</ul></div>
        </div>
      </details>
      <footer>{finding.occurrence_count?.toLocaleString?.() || finding.occurrence_count} occurrences · OpenSearch + Neptune evidence</footer>
    </article>
  )
}

function TraceItem({ item, index }) {
  return (
    <li className="trace-item">
      <span className="trace-index">{String(index + 1).padStart(2, '0')}</span>
      <div>
        <div className="trace-title"><strong>{item.tool || item.stage || 'SYSTEM'}</strong><span>{item.authority || 'guarded'}</span></div>
        <p>{item.reason || item.message || item.result}</p>
        {item.result_count !== undefined && <small>{item.result_count} bounded results</small>}
      </div>
    </li>
  )
}

function ToolApprovalCard({ plan, busy, onDecision }) {
  if (!plan) return null
  const expires = new Date(Number(plan.approval_expires_at || 0) * 1000)
  return (
    <section className="approval-card" aria-live="assertive">
      <div className="approval-heading">
        <div><p className="eyebrow">Human authority checkpoint · round {plan.approval_round}</p><h2>Approve the Agent’s MCP tool plan?</h2></div>
        <span className="approval-lock">MCP blocked</span>
      </div>
      <p className="approval-copy">Bedrock Agent selected these calls autonomously and returned control to this chat. Nothing below executes until you approve this exact hash.</p>
      <ol className="approval-tools">
        {(plan.calls || []).map((call) => (
          <li key={`${call.sequence}-${call.tool}`}>
            <span>{String(call.sequence).padStart(2, '0')}</span>
            <div><strong>{call.tool}</strong><p>{call.reason}</p><code>{JSON.stringify(call.arguments)}</code></div>
            <b>{call.authority}</b>
          </li>
        ))}
      </ol>
      <div className="approval-proof">
        <div><small>Exact plan hash</small><code>{plan.plan_hash}</code></div>
        <div><small>Approval expires</small><strong>{Number.isNaN(expires.getTime()) ? 'invalid' : expires.toLocaleTimeString()}</strong></div>
      </div>
      <div className="approval-actions">
        <button className="reject" type="button" disabled={busy} onClick={() => onDecision(false)}>Reject plan</button>
        <button className="approve" type="button" disabled={busy} onClick={() => onDecision(true)}>Approve exact plan <b>→</b></button>
      </div>
    </section>
  )
}

function App({ config, signOut, user }) {
  const [question, setQuestion] = useState(DEFAULT_QUESTION)
  const [answer, setAnswer] = useState('')
  const [findings, setFindings] = useState([])
  const [trace, setTrace] = useState([])
  const [status, setStatus] = useState('READY')
  const [error, setError] = useState('')
  const [activeChatId, setActiveChatId] = useState('')
  const [approvalPlan, setApprovalPlan] = useState(null)
  const [decisionPending, setDecisionPending] = useState(false)
  const channelRef = useRef<any>(null)
  const pollRef = useRef<any>(null)

  useEffect(() => {
    return () => {
      channelRef.current?.close?.()
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [])

  async function authHeaders(contentType = false) {
    const session = await fetchAuthSession()
    const token = session.tokens?.idToken?.toString()
    if (!token) throw new Error('Your Cognito session has expired; sign in again')
    return contentType
      ? { authorization: `Bearer ${token}`, 'content-type': 'application/json' }
      : { authorization: `Bearer ${token}` }
  }

  function handleEvent(raw) {
    const event = normalizeEvent(raw)
    if (event.type === 'token') setAnswer((current) => current + (event.text || ''))
    if (event.type === 'findings') setFindings(event.findings || [])
    if (event.type === 'trace' || event.type === 'status') setTrace((current) => [...current, event])
    if (event.type === 'approval_required') {
      setApprovalPlan(event.tool_plan || null)
      setStatus('AWAITING_APPROVAL')
    }
    if (event.type === 'complete') {
      setApprovalPlan(null)
      setStatus('COMPLETE')
      if (pollRef.current) clearInterval(pollRef.current)
      channelRef.current?.close?.()
    }
    if (event.type === 'error') {
      setError(event.message || 'The investigation failed')
      setStatus('FAILED')
      if (pollRef.current) clearInterval(pollRef.current)
      channelRef.current?.close?.()
    }
  }

  async function poll(chatId) {
    const response = await fetch(`${config.apiUrl}/api/chats/${chatId}`, {
      headers: await authHeaders(), cache: 'no-store'
    })
    if (!response.ok) return
    const state = await response.json()
    if (state.status === 'AWAITING_TOOL_APPROVAL' && state.pending_tool_plan) {
      setApprovalPlan(state.pending_tool_plan)
      setStatus('AWAITING_APPROVAL')
    } else if (state.status === 'TOOL_PLAN_APPROVED' || state.status === 'PROCESSING' || state.status === 'PLANNING') {
      setStatus('RUNNING')
    } else if (state.status === 'COMPLETE') {
      setAnswer(state.answer || answer)
      setFindings(state.findings || findings)
      setApprovalPlan(null)
      setStatus('COMPLETE')
      clearInterval(pollRef.current)
    } else if (state.status === 'REJECTED') {
      setApprovalPlan(null)
      setStatus('REJECTED')
      clearInterval(pollRef.current)
      channelRef.current?.close?.()
    } else if (state.status === 'FAILED' || state.status === 'TOOL_PLAN_EXPIRED') {
      setError(state.error_message || 'The investigation failed')
      setStatus('FAILED')
      clearInterval(pollRef.current)
    }
  }

  async function decideToolPlan(approved) {
    if (!config || !activeChatId || !approvalPlan || decisionPending) return
    setDecisionPending(true)
    try {
      const response = await fetch(`${config.apiUrl}/api/chats/${activeChatId}/tool-decision`, {
        method: 'POST',
        headers: await authHeaders(true),
        body: JSON.stringify({ approved, plan_hash: approvalPlan.plan_hash })
      })
      const payload = await response.json()
      if (!response.ok) throw new Error(payload.error || `Decision failed with ${response.status}`)
      setApprovalPlan(null)
      if (approved) {
        setStatus('RUNNING')
      } else {
        setStatus('REJECTED')
        if (pollRef.current) clearInterval(pollRef.current)
        channelRef.current?.close?.()
      }
    } catch (reason) {
      setError(String(reason))
    } finally {
      setDecisionPending(false)
    }
  }

  async function submit(event) {
    event.preventDefault()
    if (!config || !question.trim() || status === 'RUNNING' || status === 'AWAITING_APPROVAL') return
    setAnswer('')
    setFindings([])
    setTrace([])
    setError('')
    setApprovalPlan(null)
    setStatus('RUNNING')
    channelRef.current?.close?.()
    if (pollRef.current) clearInterval(pollRef.current)
    const chatId = `chat-${crypto.randomUUID()}`
    setActiveChatId(chatId)
    try {
      const currentUser = await getCurrentUser()
      if (!currentUser.userId) throw new Error('Cognito did not return a stable subject identifier')
      const channel = await events.connect(`/sessions/${currentUser.userId}/${chatId}`)
      channelRef.current = channel
      channel.subscribe({ next: handleEvent, error: (reason) => setError(String(reason)) })
      const response = await fetch(`${config.apiUrl}/api/chats`, {
        method: 'POST',
        headers: await authHeaders(true),
        body: JSON.stringify({ chat_id: chatId, query: question.trim() })
      })
      const payload = await response.json()
      if (!response.ok) throw new Error(payload.error || `Request failed with ${response.status}`)
      pollRef.current = setInterval(() => poll(chatId).catch(() => {}), 2500)
    } catch (reason) {
      setError(String(reason))
      setStatus('FAILED')
    }
  }

  return (
    <main className="app-shell">
      <aside className="reasoning-panel" aria-label="GraphRAG explanation trace">
        <div className="brand"><span className="brand-mark">G</span><div><strong>GraphRAG Ops</strong><small>HDFS anomaly lab</small></div></div>
        <section className="panel-intro">
          <p className="eyebrow">Public reasoning trace</p>
          <h2>Why each tool was called</h2>
          <p>This panel shows bounded evidence operations and authority. No private chain-of-thought is exposed.</p>
        </section>
        <ol className="trace-list">
          {trace.length ? trace.map((item, index) => <TraceItem key={`${item.sequence || index}-${index}`} item={item} index={index} />) : (
            <li className="empty-trace">Start an investigation to watch the AgentCore MCP path.</li>
          )}
        </ol>
        <div className="guardrail-card"><span className="pulse" /><div><strong>Guardrails active</strong><small>Input, tools, output, approval boundaries</small></div></div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div><p className="eyebrow">Autonomous evidence · human authority</p><h1>HDFS anomaly investigation</h1></div>
          <div className="session-controls"><span>{user?.signInDetails?.loginId || user?.username}</span><span className={`status ${status.toLowerCase()}`}>{status}</span><button type="button" onClick={signOut}>Sign out</button></div>
        </header>

        <div className="chat-scroll">
          <section className="hero">
            <span className="hero-orbit" />
            <p className="eyebrow">100 GiB GraphRAG corpus</p>
            <h2>Find the failure pattern.<br /><span>Follow the evidence.</span></h2>
            <p>FAISS/HNSW retrieval, Nova embeddings, and Neptune paths converge into three operator-ready findings.</p>
          </section>

          <ToolApprovalCard plan={approvalPlan} busy={decisionPending} onDecision={decideToolPlan} />

          {findings.length > 0 && <section className="findings"><div className="section-title"><span>Operator priorities</span><h2>Top 3 anomaly findings</h2></div>{findings.map((finding) => <FindingCard key={finding.finding_id} finding={finding} />)}</section>}

          {(answer || status === 'RUNNING') && <section className="answer-card"><p className="eyebrow">Streaming synthesis</p><div className="answer-text">{answer || <span className="typing">Selecting or executing an approved tool plan</span>}</div></section>}
          {error && <div className="error-card" role="alert">{error}</div>}
        </div>

        <form className="composer" onSubmit={submit}>
          <textarea aria-label="Ask about HDFS anomalies" value={question} onChange={(event) => setQuestion(event.target.value)} rows={2} maxLength={4000} />
          <button type="submit" disabled={!config || status === 'RUNNING' || status === 'AWAITING_APPROVAL'}><span>Investigate</span><b>↗</b></button>
          <small>Every Agent-selected MCP batch requires approval here. Remediation still requires the existing policy and exact-plan write approval workflow.</small>
        </form>
      </section>
    </main>
  )
}

function Root() {
  const [config, setConfig] = useState<any>(null)
  const [configError, setConfigError] = useState('')

  useEffect(() => {
    fetch('/runtime-config.json', { cache: 'no-store' })
      .then((response) => {
        if (!response.ok) throw new Error('Runtime configuration was not deployed')
        return response.json()
      })
      .then((loaded) => {
        if (!loaded.apiUrl || !loaded.userPoolId || !loaded.userPoolClientId || !loaded.events?.endpoint) {
          throw new Error('Runtime configuration is incomplete')
        }
        Amplify.configure({
          Auth: { Cognito: { userPoolId: loaded.userPoolId, userPoolClientId: loaded.userPoolClientId } },
          API: { Events: loaded.events },
        })
        setConfig(loaded)
      })
      .catch((reason) => setConfigError(String(reason)))
  }, [])

  if (configError) return <main className="boot-error" role="alert">{configError}</main>
  if (!config) return <main className="boot-screen">Loading the guarded GraphRAG console…</main>
  return (
    <Authenticator hideSignUp>
      {({ signOut, user }) => <App config={config} signOut={signOut} user={user} />}
    </Authenticator>
  )
}

createRoot(document.getElementById('root')!).render(<Root />)
