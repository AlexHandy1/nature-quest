import type { Species } from './MapView'

type ResultsPanelProps = {
  species: Species[]
  expandedSpecies: string | null
  onToggleSpecies: (species: string) => void
}

function ResultsPanel({ species, expandedSpecies, onToggleSpecies }: ResultsPanelProps) {
  if (species.length === 0) {
    return null
  }

  return (
    <div className="results-panel">
      <h2>Your walk</h2>
      <ol>
        {species.map((s) => {
          const isExpanded = expandedSpecies === s.species
          const primaryName = s.common_name ?? s.species
          return (
            <li key={s.species}>
              <button
                type="button"
                className="results-panel__row"
                aria-expanded={isExpanded}
                onClick={() => onToggleSpecies(s.species)}
              >
                <span className="results-panel__name">{primaryName}</span>
                {s.common_name && <span className="results-panel__sci-name">{s.species}</span>}
                <span className="results-panel__count">{s.count} observed</span>
              </button>
              {isExpanded && (
                <div className="results-panel__detail">
                  {s.image_url ? (
                    <img src={s.image_url} alt={primaryName} />
                  ) : (
                    <div className="results-panel__no-image">No image available</div>
                  )}
                  {s.species_key != null && (
                    <a
                      href={`https://www.gbif.org/species/${s.species_key}`}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      View on GBIF ↗
                    </a>
                  )}
                </div>
              )}
            </li>
          )
        })}
      </ol>
    </div>
  )
}

export default ResultsPanel
