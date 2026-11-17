import { useEffect, useState } from "react";

const SEEN_KEY = "calcipher.splashSeen";

// Original flame mark: a calorie "burn" flame with a small face. Two tones, no outlines.
export function FlameMark({ className = "" }) {
  return (
    <svg viewBox="0 0 64 80" className={className} aria-hidden="true">
      <path
        fill="#EE7A1A"
        d="M32 4c4 12 20 20 20 42 0 18-9 30-20 30S12 64 12 48c0-10 5-17 10-22 0 7 3 11 6 12-2-12 0-24 4-34z"
      />
      <path fill="#F7C548" d="M32 34c3 8 10 13 10 24 0 8-5 14-10 14s-10-6-10-14c0-8 6-14 10-24z" />
      <circle cx="28.5" cy="58" r="2" fill="#18202B" />
      <circle cx="35.5" cy="58" r="2" fill="#18202B" />
    </svg>
  );
}

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
        <FlameMark className="h-28 w-24" />
        <p className="type-label mt-4 text-5xl leading-none text-label">CalCipher</p>
        <p className="mt-3 text-base text-slate-line">Decipher your food.</p>
      </div>
    </div>
  );
}
