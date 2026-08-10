import type { Species } from './MapView'

type ResultsPanelProps = {
  species: Species[]
}

function ResultsPanel({ species }: ResultsPanelProps) {
  if (species.length === 0) {
    return null
  }

  return (
    <div className="results-panel">
      <h2>Your walk</h2>
      <ol>
        {species.map((s) => (
          <li key={s.species}>
            <span className="results-panel__name">{s.species}</span>
            <span className="results-panel__count">{s.count} observed</span>
          </li>
        ))}
      </ol>
    </div>
  )
}

export default ResultsPanel
