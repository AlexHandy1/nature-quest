import { validatePolygonPoints } from '../lib/polygon'

type DrawConfirmBarProps = {
  vertices: [number, number][]
  onConfirm: () => void
}

function DrawConfirmBar({ vertices, onConfirm }: DrawConfirmBarProps) {
  const validation = vertices.length > 0 ? validatePolygonPoints(vertices) : null
  const canConfirm = validation?.valid === true

  return (
    <div className="draw-confirm-bar">
      <button type="button" disabled={!canConfirm} onClick={onConfirm}>
        Confirm Area
      </button>
      {validation && !validation.valid && validation.reason === 'area_too_large' && (
        <p className="draw-confirm-bar__error">
          That area is too large — please draw a smaller shape (max 25 km²).
        </p>
      )}
    </div>
  )
}

export default DrawConfirmBar
