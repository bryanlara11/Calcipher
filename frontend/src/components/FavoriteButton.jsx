import { Icon } from "./Brand.jsx";

export default function FavoriteButton({ pin, saved, onToggle }) {
  if (!onToggle) return null;
  return <button type="button" className={`favorite-button${saved ? " is-saved" : ""}`}
    aria-pressed={Boolean(saved)} aria-label={`${saved ? "Remove" : "Add"} ${pin.poi_name} ${saved ? "from" : "to"} favorites`}
    onClick={() => onToggle(pin)} title={saved ? "Remove from favorites" : "Save restaurant"}>
    <Icon name="heart" size={20} fill={saved ? "currentColor" : "none"} />
  </button>;
}
