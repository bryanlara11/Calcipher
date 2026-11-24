import { useEffect, useState } from "react";
import { BrandLogo } from "./Brand.jsx";

const SEEN_KEY = "calcipher.splashSeen";

function alreadySeen() {
  try {
    return sessionStorage.getItem(SEEN_KEY) === "1";
  } catch {
    return false; // storage blocked: just show it
  }
}

// Shown once per browser tab. The app renders underneath meanwhile, so the map is ready when it fades.
export default function SplashScreen() {
  const [visible, setVisible] = useState(() => !alreadySeen());

  useEffect(() => {
    if (!visible) return;
    try {
      sessionStorage.setItem(SEEN_KEY, "1");
    } catch {
      /* ignore */
    }
    // animationend never fires if animations don't run (background tab, animations disabled),
    // so a timer guarantees the splash can't get stuck over the app.
    const fallback = setTimeout(() => setVisible(false), 2600);
    return () => clearTimeout(fallback);
  }, [visible]);

  if (!visible) return null;
  return (
    <div
      className="splash fixed inset-0 z-[2000] grid place-items-center bg-ink px-4"
      aria-hidden="true"
      onClick={() => setVisible(false)}
      onAnimationEnd={(e) => {
        if (e.target === e.currentTarget && e.animationName === "splash-out") setVisible(false);
      }}
    >
      <div className="splash-content flex flex-col items-center text-center">
        <BrandLogo />
        <p className="mt-7 font-serif text-3xl text-ink">Good food. A little more insight.</p>
        <p className="mt-3 text-base text-slate-mute">Decipher your food.</p>
      </div>
    </div>
  );
}
