/** The talk button's character: a small, friendly microphone with a face (inline SVG, no assets).
 * The mood drives colours and animations in styles.css (`.buddy.mood-*`). */
export type Mood = "ready" | "playing" | "listening" | "hearing" | "thinking" | "speaking" | "resuming" | "idle";

export function Buddy({ mood }: { mood: Mood }) {
  const happy = mood === "resuming" || mood === "ready";
  return (
    <svg
      className={`buddy mood-${mood}`}
      viewBox="0 0 160 160"
      width="148"
      height="148"
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <linearGradient id="buddy-body" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" className="buddy-c1" />
          <stop offset="1" className="buddy-c2" />
        </linearGradient>
      </defs>
      {/* halo behind the character (pulses) */}
      <circle className="buddy-halo" cx="80" cy="74" r="62" />
      {/* sound waves (listening) */}
      <g className="buddy-waves" fill="none" strokeLinecap="round" strokeWidth="5">
        <path className="w1" d="M30 56 Q20 74 30 92" />
        <path className="w2" d="M18 46 Q4 74 18 102" />
        <path className="w1" d="M130 56 Q140 74 130 92" />
        <path className="w2" d="M142 46 Q156 74 142 102" />
      </g>
      {/* thinking dots */}
      <g className="buddy-dots">
        <circle className="d1" cx="62" cy="14" r="5" />
        <circle className="d2" cx="80" cy="10" r="5" />
        <circle className="d3" cx="98" cy="14" r="5" />
      </g>
      <g className="buddy-bob">
        {/* stand */}
        <path
          className="buddy-stand"
          d="M46 86 Q46 120 80 120 Q114 120 114 86"
          fill="none"
          strokeWidth="6"
          strokeLinecap="round"
        />
        <rect className="buddy-stand-fill" x="76" y="120" width="8" height="18" rx="3" />
        <rect className="buddy-stand-fill" x="58" y="136" width="44" height="8" rx="4" />
        {/* body (mic capsule) */}
        <rect x="52" y="26" width="56" height="84" rx="28" fill="url(#buddy-body)" />
        {/* grille highlight */}
        <path
          d="M60 46 Q60 34 72 31"
          fill="none"
          stroke="rgba(255,255,255,0.45)"
          strokeWidth="4"
          strokeLinecap="round"
        />
        {/* face */}
        <g className="buddy-face">
          {happy ? (
            <g fill="none" stroke="#1b2130" strokeWidth="4" strokeLinecap="round">
              <path d="M64 63 Q69 57 74 63" />
              <path d="M86 63 Q91 57 96 63" />
            </g>
          ) : (
            <g className="buddy-eyes" fill="#1b2130">
              <ellipse cx="69" cy="62" rx="5" ry="6.5" />
              <ellipse cx="91" cy="62" rx="5" ry="6.5" />
              <circle cx="70.5" cy="60" r="1.8" fill="#fff" />
              <circle cx="92.5" cy="60" r="1.8" fill="#fff" />
            </g>
          )}
          <circle cx="61" cy="74" r="4.5" fill="#ff8fa3" opacity="0.75" />
          <circle cx="99" cy="74" r="4.5" fill="#ff8fa3" opacity="0.75" />
          {mood === "speaking" || mood === "hearing" || mood === "listening" ? (
            <ellipse className="buddy-mouth-open" cx="80" cy="80" rx="7" ry="6" fill="#1b2130" />
          ) : mood === "thinking" ? (
            <path d="M73 81 L87 79" stroke="#1b2130" strokeWidth="4" strokeLinecap="round" />
          ) : (
            <path d="M70 76 Q80 86 90 76" fill="none" stroke="#1b2130" strokeWidth="4" strokeLinecap="round" />
          )}
        </g>
      </g>
      {/* play badge (ready) */}
      {mood === "ready" && (
        <g className="buddy-play">
          <circle cx="122" cy="118" r="20" />
          <path d="M116 107 L132 118 L116 129 Z" fill="#fff" />
        </g>
      )}
    </svg>
  );
}

/** Small mic glyph for the talk pill. */
export function MicIcon() {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" focusable="false">
      <rect x="8" y="2.5" width="8" height="12" rx="4" fill="currentColor" />
      <path
        d="M5 11a7 7 0 0 0 14 0M12 18v3.5M8.5 21.5h7"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  );
}
