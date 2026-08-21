import { useEffect } from 'react'
import { Canvas } from '@react-three/fiber'
import scenarioData from '@scenario'
import type { Scenario } from './types'
import { useSimStore } from './store/simStore'
import { World } from './scene/World'
import { BottomControls, LeftPanel, RightPanel } from './ui/HUD'
import { fetchPlan, runPlan } from './lib/api'
import './App.css'

const scenario = scenarioData as Scenario

export default function App() {
  const loadScenario = useSimStore((s) => s.loadScenario)
  const reset = useSimStore((s) => s.reset)
  const setPlan = useSimStore((s) => s.setPlan)
  const appendLog = useSimStore((s) => s.appendLog)
  const setError = useSimStore((s) => s.setError)

  useEffect(() => {
    loadScenario(scenario)
  }, [loadScenario])

  const onExecute = async () => {
    try {
      setError(null)
      reset()
      appendLog({ text: '[AI] Requesting plan from /api/solve ...', level: 'info' })
      const response = await fetchPlan(scenario)
      setPlan(response)
      if (!response.solution_found) {
        appendLog({
          text: `[AI] FAILURE: ${response.message ?? 'no solution'}`,
          level: 'error',
        })
        return
      }
      appendLog({
        text: `[AI] Plan received - ${response.steps.length} steps, cost ${response.total_cost}`,
        level: 'ok',
      })
      // small delay so reset state settles, then run
      await new Promise((r) => setTimeout(r, 200))
      await runPlan()
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setError(msg)
      appendLog({ text: `[AI] API ERROR: ${msg}`, level: 'error' })
    }
  }

  return (
    <div className="app-shell">
      <div className="viewport">
        <Canvas
          shadows
          camera={{ position: [16, 14, 18], fov: 42, near: 0.1, far: 200 }}
        >
          <World />
        </Canvas>
      </div>
      <LeftPanel />
      <RightPanel />
      <BottomControls onExecute={onExecute} onReset={reset} />
    </div>
  )
}
