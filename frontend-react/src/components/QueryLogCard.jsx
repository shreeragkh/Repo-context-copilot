import { useState } from 'react'
import { ChevronDown, ChevronUp, Zap, BarChart2 } from 'lucide-react'
import './QueryLogCard.css'

function ScoreBar({ score, color }) {
  const pct = score != null ? Math.round(score * 100) : null
  const fillColor = score >= 0.75 ? '#34d399' : score >= 0.5 ? '#fbbf24' : '#f87171'
  return (
    <div className="score-bar-row">
      <div className="score-bar">
        <div
          className="score-bar-fill"
          style={{ width: pct != null ? `${pct}%` : '0%', background: fillColor }}
        />
      </div>
      <span className="score-val" style={{ color: pct != null ? fillColor : 'var(--text-muted)' }}>
        {pct != null ? `${pct}%` : '—'}
      </span>
    </div>
  )
}

function EvalStatus({ status }) {
  if (!status || status === 'skipped') return null
  const map = {
    pending: { icon: '⏳', label: 'Scoring…',  color: 'var(--accent-blue)'  },
    done:    { icon: '✅', label: 'Scored',     color: 'var(--accent-green)' },
    error:   { icon: '⚠️', label: 'Score error', color: 'var(--accent-amber)' },
  }
  const s = map[status] || map.pending
  return (
    <span className="eval-status-badge" style={{ color: s.color }}>
      {s.icon} {s.label}
    </span>
  )
}

export default function QueryLogCard({ log }) {
  const [open, setOpen] = useState(false)

  const tier        = log.model_tier?.toUpperCase() || 'FREE'
  const badgeClass  = tier === 'PAID' ? 'badge-paid' : 'badge-free'
  const complexCls  = `badge-${(log.complexity || 'medium').toLowerCase()}`
  const redPct      = log.token_reduction_pct
  const hasEval     = log.adaptive_score != null || log.baseline_score != null

  return (
    <div className="qlc-card">
      {/* Header row */}
      <button className="qlc-header" onClick={() => setOpen(o => !o)}>
        <span className="qlc-time">{log.timestamp?.slice(11, 19)}</span>
        <span className="qlc-query">{log.query}</span>
        <div className="qlc-badges">
          <span className={`badge ${complexCls}`}>{log.complexity}</span>
          <span className={`badge ${badgeClass}`}>{tier}</span>
          {redPct != null && (
            <span className="qlc-reduction">
              {redPct > 0 ? `↓${redPct}% tokens` : `${redPct}% ctx`}
            </span>
          )}
          {hasEval && <EvalStatus status={log.eval_status} />}
        </div>
        {open ? <ChevronUp size={14} className="qlc-chevron" /> : <ChevronDown size={14} className="qlc-chevron" />}
      </button>

      {open && (
        <div className="qlc-body">
          {/* Stats row */}
          <div className="qlc-stats">
            <div className="qlc-stat"><span className="qlc-stat-lbl">Model</span><code>{log.model_name}</code></div>
            <div className="qlc-stat"><span className="qlc-stat-lbl">Latency</span>{log.latency_ms} ms</div>
            <div className="qlc-stat"><span className="qlc-stat-lbl">Cache Hit</span>{log.cache_hit ? '✅ Yes' : '❌ No'}</div>
            <div className="qlc-stat"><span className="qlc-stat-lbl">Adap. Chunks</span>{log.adaptive_chunks_used}</div>
            <div className="qlc-stat"><span className="qlc-stat-lbl">Base. Chunks</span>{log.baseline_chunks_used ?? '—'}</div>
            <div className="qlc-stat"><span className="qlc-stat-lbl">Adap. Tokens</span>{log.adaptive_tokens}</div>
            <div className="qlc-stat"><span className="qlc-stat-lbl">Base. Tokens</span>{log.baseline_tokens ?? '—'}</div>
            <div className="qlc-stat"><span className="qlc-stat-lbl">Reduction</span>{redPct != null ? `${redPct}%` : '—'}</div>
          </div>

          {/* Eval scores */}
          {hasEval && (
            <div className="qlc-eval">
              <div className="qlc-eval-title"><BarChart2 size={13} /> LLM-as-Judge Scores</div>
              <div className="qlc-eval-rows">
                <div className="qlc-eval-row">
                  <span className="qlc-eval-lbl"><Zap size={11} /> Adaptive</span>
                  <ScoreBar score={log.adaptive_score} />
                </div>
                <div className="qlc-eval-row">
                  <span className="qlc-eval-lbl">📏 Baseline</span>
                  <ScoreBar score={log.baseline_score} />
                </div>
                {log.adaptive_score != null && log.baseline_score != null && log.baseline_score > 0 && (
                  <div className="qlc-accuracy-retained">
                    Accuracy retained:&nbsp;
                    <strong style={{color: 'var(--accent-green)'}}>
                      {Math.min(100, Math.round(100 * log.adaptive_score / log.baseline_score))}%
                    </strong>
                  </div>
                )}
                <EvalStatus status={log.eval_status} />
              </div>
            </div>
          )}

          {/* Answers */}
          <div className="qlc-answers">
            <div className="qlc-answer adaptive">
              <div className="qlc-answer-label"><Zap size={12} /> Adaptive Answer</div>
              <div className="qlc-answer-text">{log.adaptive_answer}</div>
            </div>
            {log.baseline_answer && (
              <div className="qlc-answer baseline">
                <div className="qlc-answer-label">📏 Baseline Answer</div>
                <div className="qlc-answer-text">{log.baseline_answer}</div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
