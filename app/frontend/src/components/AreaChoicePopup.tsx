type AreaChoicePopupProps = {
  onSelectFixed: () => void
  onSelectDraw: () => void
}

function AreaChoicePopup({ onSelectFixed, onSelectDraw }: AreaChoicePopupProps) {
  return (
    <div className="area-choice-popup">
      <h2>Choose an area</h2>
      <button type="button" onClick={onSelectFixed}>
        Explore Retiro Park
      </button>
      <button type="button" onClick={onSelectDraw}>
        Draw your own area
      </button>
    </div>
  )
}

export default AreaChoicePopup
