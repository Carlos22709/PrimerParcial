import { OrbitControls } from '@react-three/drei'
import { useMemo } from 'react'
import { buildWalkable, cellKey, cellToWorld, generateWalls } from '../lib/grid'
import { useSimStore } from '../store/simStore'
import type { Scenario } from '../types'
import { Robot } from './Robot'
import {
  ChargerVoxel,
  KeyVoxel,
  MaterialVoxel,
  PanelVoxel,
  SlidingDoor,
  StationVoxel,
  ToolVoxel,
} from './Entities'

/** Corridor MOVE cost -> distinct floor tint. */
export function costToFloorColor(cost: number): string {
  // Discrete palette keyed by known corridor costs.
  const palette: Record<number, string> = {
    3: '#7dd3fc', // sky, cheapest
    4: '#86efac', // green
    5: '#fde047', // yellow
    6: '#fdba74', // orange
    8: '#f472b6', // pink
    12: '#c084fc', // violet, expensive alternate
    14: '#a78bfa',
  }
  if (palette[cost]) return palette[cost]

  // Fallback for any other cost: map 1..20 onto a clear hue wheel.
  const t = Math.min(1, Math.max(0, (cost - 1) / 19))
  const hue = 190 - t * 190
  return `hsl(${hue} 82% 64%)`
}

const ROOM_FLOOR = '#f4f7fb'

function buildCellCostMap(scenario: Scenario): Map<string, number> {
  const map = new Map<string, number>()
  const corridorSet = new Set(
    scenario.layout.corridor_cells.map(([x, z]) => cellKey(x, z)),
  )

  for (const c of scenario.corridors) {
    // paint each undirected edge once
    if (c.from > c.to) continue
    const path = scenario.layout.paths[`${c.from}-${c.to}`]
    if (!path) continue
    for (const [gx, gz] of path) {
      const key = cellKey(gx, gz)
      // Only tint dedicated corridor/bridge cells (not room interiors)
      if (!corridorSet.has(key)) continue
      const prev = map.get(key)
      // MIN cost on overlap: a door cell shared with the long alternate
      // must keep its short-corridor cost (e.g. DOOR3=3), not inherit 12.
      if (prev === undefined || c.cost < prev) map.set(key, c.cost)
    }
  }

  // Second pass: cells exclusive to the expensive alternate keep that cost.
  // Already handled by min; exclusive spine cells only appear on Z2-Z5.
  return map
}

function Floors() {
  const scenario = useSimStore((s) => s.scenario)
  const tiles = useMemo(() => {
    if (!scenario) return []
    const layout = scenario.layout
    const walkable = buildWalkable(layout)
    const costs = buildCellCostMap(scenario)
    const cs = layout.cell_size
    return [...walkable].map((key) => {
      const [gx, gz] = key.split(',').map(Number)
      const [x, , z] = cellToWorld(gx, gz, cs, 0)
      const cost = costs.get(key)
      return {
        key,
        x,
        z,
        cs,
        color: cost !== undefined ? costToFloorColor(cost) : ROOM_FLOOR,
        cost,
      }
    })
  }, [scenario])

  if (!scenario) return null
  return (
    <group>
      {tiles.map((t) => (
        <mesh key={t.key} position={[t.x, -0.05, t.z]}>
          <boxGeometry args={[t.cs * 0.98, 0.1, t.cs * 0.98]} />
          <meshStandardMaterial color={t.color} roughness={0.62} metalness={0.04} />
        </mesh>
      ))}
    </group>
  )
}

function Walls() {
  const layout = useSimStore((s) => s.scenario?.layout)
  const walls = useMemo(() => (layout ? generateWalls(layout) : []), [layout])
  return (
    <group>
      {walls.map((w, i) => (
        <mesh key={i} position={[w.x, w.y, w.z]}>
          <boxGeometry args={[w.w, w.h, w.d]} />
          <meshStandardMaterial color="#d7dce8" roughness={0.55} metalness={0.05} />
        </mesh>
      ))}
    </group>
  )
}

function ZoneMarkers() {
  const zones = useSimStore((s) => s.scenario?.zones ?? [])
  const centers = useSimStore((s) => s.scenario?.layout.centers ?? {})
  return (
    <group>
      {zones.map((z) => {
        const c = centers[z.id]
        if (!c) return null
        return (
          <mesh key={z.id} position={[c[0], 0.02, c[2]]} rotation={[-Math.PI / 2, 0, 0]}>
            <circleGeometry args={[0.3, 16]} />
            <meshBasicMaterial color="#2563eb" transparent opacity={0.22} />
          </mesh>
        )
      })}
    </group>
  )
}

export function World() {
  const scenario = useSimStore((s) => s.scenario)
  if (!scenario) return null

  return (
    <>
      <color attach="background" args={['#eef7ff']} />
      <ambientLight intensity={0.98} />
      <directionalLight position={[14, 20, 10]} intensity={1.1} color="#fff7ed" />
      <directionalLight position={[-10, 8, -8]} intensity={0.32} color="#f0abfc" />
      <hemisphereLight args={['#f8fafc', '#93c5fd', 0.56]} />

      <Floors />
      <Walls />
      <ZoneMarkers />

      {scenario.doors.map((d) => (
        <SlidingDoor key={d.id} id={d.id} color={d.color} />
      ))}
      {scenario.keys.map((k) => (
        <KeyVoxel key={k.id} id={k.id} color={k.color} />
      ))}
      {scenario.tools.map((t) => (
        <ToolVoxel key={t.id} id={t.id} />
      ))}
      {scenario.materials.map((m) => (
        <MaterialVoxel key={m.type} type={m.type} />
      ))}
      {scenario.panels.map((p) => (
        <PanelVoxel key={p.id} id={p.id} />
      ))}
      {scenario.stations.map((st) => (
        <StationVoxel key={st.id} id={st.id} />
      ))}
      {scenario.chargers.map((c) => (
        <ChargerVoxel key={c.id} id={c.id} />
      ))}

      <Robot />

      <OrbitControls
        makeDefault
        target={[6.5, 0, 9]}
        maxPolarAngle={Math.PI / 2.15}
        minDistance={6}
        maxDistance={45}
      />
    </>
  )
}
