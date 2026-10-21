import type { Species } from './MapView'
import NarrationControl from './NarrationControl'

type ResultsPanelProps = {
  species: Species[]
  expandedSpecies: string | null
  onToggleSpecies: (species: string) => void
  distinctId: string
}

// upload.wikimedia.org serves Commons originals; thumb.wikimedia.org is what
// the REST summary API's thumbnail/originalimage fields actually point at.
const TRUSTED_IMAGE_HOSTS = new Set(['upload.wikimedia.org', 'thumb.wikimedia.org'])

function isTrustedImageUrl(url: string): boolean {
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'https:' && TRUSTED_IMAGE_HOSTS.has(parsed.hostname)
  } catch {
    return false
  }
}

function ResultsPanel({ species, expandedSpecies, onToggleSpecies, distinctId }: ResultsPanelProps) {
  if (species.length === 0) {
    return null
  }

  return (
    <div className="results-panel">
      <div className="results-panel__header">
        <h2>Your walk</h2>
        <NarrationControl
          key={species.map((s) => s.species).join('|')}
          species={species}
          distinctId={distinctId}
        />
      </div>
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
                <span className="results-panel__counts">
                  <span className="results-panel__count">{s.hotspot_count} observed in this hotspot</span>
                  <span className="results-panel__count">{s.count} observed in your whole search area</span>
                </span>
              </button>
              {isExpanded && (
                <div className="results-panel__detail">
                  {s.image_url && isTrustedImageUrl(s.image_url) ? (
                    <img src={s.image_url} alt={primaryName} />
                  ) : (
                    <div className="results-panel__no-image">No image available</div>
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
