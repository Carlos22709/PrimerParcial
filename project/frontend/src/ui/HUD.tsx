import type { PayloadItem } from '../types'
import { COLOR_MAP } from '../types'
import { useSimStore } from '../store/simStore'

function PanelShell({
  title,
  children,
  className = '',
}: {
  title: string
  children: React.ReactNode
  className?: string
}) {
  return (
    <div className={`hud-panel ${className}`}>
      <div className="hud-panel-title">{title}</div>
      {children}
    </div>
  )
}

export function LeftPanel() {
  const battery = useSimStore((s) => s.runtime?.battery ?? 0)
  const batteryMax = useSimStore((s) => s.scenario?.robot.battery_max ?? 100)
  const payload = useSimStore((s) => s.runtime?.payload ?? [])
  const capacity = useSimStore((s) => s.scenario?.robot.cargo_capacity ?? 3)
  const pct = Math.round((battery / batteryMax) * 100)

  return (
    <aside className="hud-left">
      <div className="brand">
        <div className="brand-kicker">EMERGENCY CONTROL</div>
        <div className="brand-title">AI OPERATIONS UNIT</div>
      </div>

      <PanelShell title="POWER CORE">
        <div className="battery-row">
          <div className="battery-track">
            <div
              className="battery-fill"
              style={{
                width: `${pct}%`,
                background:
                  pct > 40
                    ? 'linear-gradient(90deg,#10b981,#14b8d4,#2563eb)'
                    : 'linear-gradient(90deg,#f97316,#ef4444,#ec4899)',
              }}
            />
          </div>
          <span className="battery-pct">{pct}%</span>
        </div>
        <div className="muted">Battery {battery}/{batteryMax}</div>
      </PanelShell>

      <PanelShell title="PAYLOAD">
        <div className="payload-slots">
          {Array.from({ length: capacity }).map((_, i) => {
            const item = payload[i]
            return (
              <div key={i} className={`payload-slot ${item ? 'filled' : ''}`}>
                {item ? <PayloadLabel item={item} /> : <span className="muted">empty</span>}
              </div>
            )
          })}
        </div>
        {payload.length === 0 && <div className="muted">No items equipped.</div>}
      </PanelShell>

      <PanelShell title="MAP LEGEND">
        <ul className="legend">
          <li><span className="dot" style={{ background: COLOR_MAP.yellow }} /> Keys</li>
          <li><span className="dot" style={{ background: COLOR_MAP.cyan }} /> Doors (translucent)</li>
          <li><span className="dot" style={{ background: COLOR_MAP.blue }} /> Panel OK</li>
          <li><span className="dot" style={{ background: COLOR_MAP.red }} /> Panel DAMAGED / Station OFFLINE</li>
          <li><span className="dot" style={{ background: COLOR_MAP.green }} /> Station ONLINE</li>
          <li><span className="dot" style={{ background: '#fbbf24' }} /> Charger</li>
          <li><span className="dot" style={{ background: '#fb923c' }} /> Tools</li>
          <li className="legend-cost">
            <span className="cost-bar" /> Floor tint = corridor MOVE cost (each cost = distinct color)
          </li>
        </ul>
      </PanelShell>
    </aside>
  )
}

function PayloadLabel({ item }: { item: PayloadItem }) {
  if (item.kind === 'key') {
    return (
      <span style={{ color: COLOR_MAP[item.color] ?? '#fff' }}>
        KEY {item.id}
      </span>
    )
  }
  if (item.kind === 'tool') {
    return <span style={{ color: '#fb923c' }}>{item.id}</span>
  }
  return <span style={{ color: '#a78bfa' }}>{item.type}</span>
}

export function RightPanel() {
  const energySpent = useSimStore((s) => s.runtime?.energySpent ?? 0)
  const totalCost = useSimStore((s) => s.totalCost)
  const log = useSimStore((s) => s.log)
  const stepIndex = useSimStore((s) => s.stepIndex)
  const planLen = useSimStore((s) => s.plan.length)
  const error = useSimStore((s) => s.error)

  return (
    <aside className="hud-right">
      <PanelShell title="ENERGY COST" className="energy-cost">
        <div className="energy-big">{energySpent}</div>
        {totalCost > 0 && <div className="muted">Plan total: {totalCost}</div>}
      </PanelShell>

      <PanelShell title="EXECUTION LOG" className="log-panel">
        <div className="step-counter">
          STEP {stepIndex}/{planLen || 0}
        </div>
        {error && <div className="log-error-banner">{error}</div>}
        <div className="log-scroll">
          {log.map((e) => (
            <div key={e.index} className={`log-line log-${e.level}`}>
              {e.text}
            </div>
          ))}
        </div>
      </PanelShell>
    </aside>
  )
}

export function BottomControls({
  onExecute,
  onReset,
}: {
  onExecute: () => void
  onReset: () => void
}) {
  const running = useSimStore((s) => s.running)
  const speed = useSimStore((s) => s.speed)
  const setSpeed = useSimStore((s) => s.setSpeed)
  const setRunning = useSimStore((s) => s.setRunning)
  const zone = useSimStore((s) => s.runtime?.robotZone)
  const zoneName = useSimStore(
    (s) => s.scenario?.zones.find((z) => z.id === s.runtime?.robotZone)?.name,
  )

  return (
    <footer className="hud-bottom">
      <button className="btn btn-primary" onClick={onExecute} disabled={running}>
        EXECUTE PLAN
      </button>
      <button
        className="btn btn-secondary"
        onClick={() => {
          setRunning(false)
          onReset()
        }}
      >
        RESET
      </button>
      <label className="speed-control">
        SPEED
        <input
          type="range"
          min={0.5}
          max={3}
          step={0.25}
          value={speed}
          onChange={(e) => setSpeed(Number(e.target.value))}
        />
        <span>{speed.toFixed(2)}x</span>
      </label>
      <div className="zone-badge">
        ZONE <strong>{zone}</strong> / {zoneName}
      </div>
    </footer>
  )
}
