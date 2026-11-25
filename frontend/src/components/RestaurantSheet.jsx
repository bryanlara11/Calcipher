import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { CloseButton } from "./Brand.jsx";
import "../restaurant-sheet.css";

// Phones get a bottom sheet for a tapped restaurant instead of a map balloon: a balloon is limited
// to a slice of the map, so its card had to scroll sideways and up/down inside a ~300 px box.
export const PHONE_QUERY = "(max-width: 760px)";

export function useIsPhone() {
  const query = () => typeof window !== "undefined" && Boolean(window.matchMedia?.(PHONE_QUERY).matches);
  const [phone, setPhone] = useState(query);
  useEffect(() => {
    const list = window.matchMedia?.(PHONE_QUERY);
    if (!list) return undefined;
    const update = () => setPhone(list.matches);
    list.addEventListener("change", update);
    return () => list.removeEventListener("change", update);
  }, []);
  return phone;
}

export default function RestaurantSheet({ title, onClose, children }) {
  const panel = useRef(null);
  const drag = useRef(null);
  useEffect(() => {
    const onKey = event => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    panel.current?.focus({ preventScroll: true });
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Swipe the handle area down to dismiss, like a system sheet.
  const onTouchStart = event => { drag.current = event.touches[0].clientY; };
  const onTouchEnd = event => {
    if (drag.current != null && event.changedTouches[0].clientY - drag.current > 60) onClose();
    drag.current = null;
  };

  return createPortal(
    <section ref={panel} className="restaurant-sheet" role="dialog" aria-label={title} tabIndex={-1}>
      <header className="restaurant-sheet-handle" onTouchStart={onTouchStart} onTouchEnd={onTouchEnd}>
        <span className="sheet-grabber" aria-hidden="true" />
        <CloseButton label="Close restaurant details" onClick={onClose} className="sheet-close" />
      </header>
      <div className="restaurant-sheet-body">{children}</div>
    </section>,
    document.body,
  );
}
