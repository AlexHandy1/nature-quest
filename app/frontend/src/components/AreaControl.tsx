type AreaControlProps = {
  mode: 'fixed' | 'draw'
  onSelectFixed: () => void
  onStartDraw: () => void
}

function AreaControl({ mode, onSelectFixed, onStartDraw }: AreaControlProps) {
  return (
    <div className="area-control">
      <span className="area-control__label">
        Exploring: {mode === 'fixed' ? 'Retiro Park' : 'Custom area'}
      </span>
      {mode === 'fixed' && (
        <button type="button" onClick={onStartDraw}>
          Draw your own area
        </button>
      )}
      {mode === 'draw' && (
        <>
          <button type="button" onClick={onStartDraw}>
            Redraw area
          </button>
          <button type="button" onClick={onSelectFixed}>
            Explore Retiro Park
          </button>
        </>
      )}
    </div>
  )
}

export default AreaControl
