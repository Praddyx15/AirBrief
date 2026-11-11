import { cn } from "@/lib/utils";

// Landing hero illustration. Zones are colour + hatch + label, never colour alone; the fixed
// viewBox ratio reserves the space, so no layout shift.

const W = 560;
const H = 420;

function hexPoints(cx: number, cy: number, r: number): string {
  const pts: string[] = [];
  for (let i = 0; i < 6; i++) {
    const a = (Math.PI / 3) * i + Math.PI / 6;
    pts.push(`${(cx + r * Math.cos(a)).toFixed(1)},${(cy + r * Math.sin(a)).toFixed(1)}`);
  }
  return pts.join(" ");
}

const HEX_R = 13;
const HEX_DX = Math.sqrt(3) * HEX_R;
const HEX_DY = 1.5 * HEX_R;

/** [col, row, tone] for the flyability grid (bottom right). */
const HEX_CELLS: readonly [number, number, "ok" | "warn" | "danger"][] = [
  [0, 0, "ok"],
  [1, 0, "ok"],
  [2, 0, "warn"],
  [3, 0, "warn"],
  [0, 1, "ok"],
  [1, 1, "ok"],
  [2, 1, "warn"],
  [3, 1, "danger"],
  [0, 2, "ok"],
  [1, 2, "ok"],
  [2, 2, "ok"],
  [3, 2, "warn"],
];

const HEX_CLASS: Record<"ok" | "warn" | "danger", string> = {
  ok: "fill-ok/25 stroke-ok",
  warn: "fill-warn/25 stroke-warn",
  danger: "fill-danger/30 stroke-danger",
};

/** Survey polygon and its flight lines (clipped to the polygon with a clipPath). */
const SURVEY = "M70,250 L215,228 L248,318 L190,372 L84,352 Z";
const SURVEY_LINES = Array.from({ length: 11 }, (_, i) => 236 + i * 13);

const AERODROME = { x: 392, y: 150 };

export function HeroMap({ className }: { className?: string }) {
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      role="img"
      aria-labelledby="hero-map-title hero-map-desc"
      className={cn("block h-auto w-full", className)}
      preserveAspectRatio="xMidYMid meet"
    >
      <title id="hero-map-title">Illustration of an AirBrief map</title>
      <desc id="hero-map-desc">
        An aerodrome surrounded by red, yellow and green airspace rings, a survey area with parallel flight lines, a
        flight track, and a hexagonal flyability grid.
      </desc>
      <defs>
        <pattern id="hero-hatch-red" width="8" height="8" patternUnits="userSpaceOnUse">
          <path d="M0,0 L8,8 M8,0 L0,8" className="stroke-zone-red" strokeWidth="1" />
        </pattern>
        <pattern id="hero-hatch-yellow" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <path d="M0,0 L0,8" className="stroke-zone-yellow" strokeWidth="1.2" />
        </pattern>
        <pattern id="hero-dots-green" width="10" height="10" patternUnits="userSpaceOnUse">
          <circle cx="5" cy="5" r="1.1" className="fill-zone-green" />
        </pattern>
        <pattern id="hero-grid" width="40" height="40" patternUnits="userSpaceOnUse">
          <path d="M40,0 L0,0 0,40" fill="none" className="stroke-border" strokeWidth="0.75" />
        </pattern>
        <clipPath id="hero-survey-clip">
          <path d={SURVEY} />
        </clipPath>
        <clipPath id="hero-frame">
          <rect x="0" y="0" width={W} height={H} rx="6" />
        </clipPath>
      </defs>

      <g clipPath="url(#hero-frame)">
        <rect x="0" y="0" width={W} height={H} className="fill-surface" />
        <rect x="0" y="0" width={W} height={H} fill="url(#hero-grid)" />

        {/* terrain contours */}
        <g fill="none" className="stroke-fg-muted/25" strokeWidth="1">
          <path d="M-10,90 C80,60 150,120 240,95 S380,40 470,70 S560,40 580,50" />
          <path d="M-10,120 C90,95 160,150 250,125 S370,80 460,100 S560,80 580,85" />
          <path d="M-10,400 C60,380 120,410 200,395 S330,350 420,380 S540,400 580,385" />
          <path d="M-10,372 C70,350 130,385 210,368 S320,330 410,352 S540,370 580,358" />
        </g>

        {/* lake */}
        <path
          d="M276,214 C300,198 338,206 344,228 C350,250 320,266 296,258 C272,250 256,228 276,214 Z"
          className="fill-accent/15 stroke-accent/50"
          strokeWidth="1"
        />

        {/* airspace rings: green (outer), yellow, red (inner) */}
        <circle cx={AERODROME.x} cy={AERODROME.y} r="150" className="fill-zone-green/10 stroke-zone-green/70" strokeWidth="1.25" />
        <circle cx={AERODROME.x} cy={AERODROME.y} r="150" fill="url(#hero-dots-green)" opacity="0.5" />
        <circle cx={AERODROME.x} cy={AERODROME.y} r="96" className="fill-zone-yellow/15 stroke-zone-yellow" strokeWidth="1.25" />
        <circle cx={AERODROME.x} cy={AERODROME.y} r="96" fill="url(#hero-hatch-yellow)" opacity="0.45" />
        <circle cx={AERODROME.x} cy={AERODROME.y} r="48" className="fill-zone-red/25 stroke-zone-red" strokeWidth="1.5" />
        <circle cx={AERODROME.x} cy={AERODROME.y} r="48" fill="url(#hero-hatch-red)" opacity="0.6" />

        {/* runway */}
        <g transform={`rotate(-28 ${AERODROME.x} ${AERODROME.y})`}>
          <rect x={AERODROME.x - 34} y={AERODROME.y - 3.5} width="68" height="7" rx="1.5" className="fill-fg" />
          <path
            d={`M${AERODROME.x - 28},${AERODROME.y} H${AERODROME.x + 28}`}
            className="stroke-bg"
            strokeWidth="1"
            strokeDasharray="4 4"
          />
        </g>

        {/* zone labels */}
        <g className="fill-fg font-mono" fontSize="11">
          <text x={AERODROME.x + 10} y={AERODROME.y + 34}>
            RED
          </text>
          {/* upper right of the yellow ring, clear of the flight-track label below it */}
          <text x={AERODROME.x + 34} y={AERODROME.y - 62}>
            YELLOW
          </text>
          <text x={AERODROME.x + 64} y={AERODROME.y + 134}>
            GREEN
          </text>
          <text x={AERODROME.x - 26} y={AERODROME.y - 16} className="fill-fg-muted">
            VOXX
          </text>
        </g>

        {/* survey area with flight lines */}
        <path d={SURVEY} className="fill-accent/15 stroke-accent" strokeWidth="1.75" />
        <g clipPath="url(#hero-survey-clip)" className="stroke-accent/70" strokeWidth="1" strokeDasharray="5 4">
          {SURVEY_LINES.map((y) => (
            <path key={y} d={`M40,${y} L280,${y - 14}`} />
          ))}
        </g>
        <text x="96" y="296" className="fill-fg font-mono" fontSize="11">
          1.19 km²
        </text>
        <text x="96" y="310" className="fill-fg-muted font-mono" fontSize="10">
          GSD 2.74 cm/px
        </text>

        {/* flight track (dashed beyond the limit) */}
        <path
          d="M232,300 C270,300 290,300 305,286 S330,250 352,236"
          fill="none"
          className="stroke-ok"
          strokeWidth="2.25"
          strokeLinecap="round"
        />
        <path
          d="M352,236 C372,224 396,212 418,206"
          fill="none"
          className="stroke-danger"
          strokeWidth="2.25"
          strokeLinecap="round"
          strokeDasharray="6 4"
        />
        <circle cx="232" cy="300" r="4" className="fill-bg stroke-ok" strokeWidth="2" />
        <path d="M418,206 l-7,-3 l2,7 z" className="fill-danger" />
        <text x="420" y="224" className="fill-fg font-mono" fontSize="10">
          135 m AGL &gt; 120 m
        </text>

        {/* flyability hex grid */}
        <g transform="translate(404 330)" strokeWidth="1">
          {HEX_CELLS.map(([c, r, tone]) => (
            <polygon
              key={`${c}-${r}`}
              points={hexPoints(c * HEX_DX + (r % 2 ? HEX_DX / 2 : 0), r * HEX_DY, HEX_R - 0.6)}
              className={HEX_CLASS[tone]}
            />
          ))}
        </g>

        {/* north arrow and scale */}
        <g transform="translate(28 34)" className="fill-fg-muted">
          <path d="M0,-14 L6,4 L0,0 L-6,4 Z" className="fill-fg" />
          <text x="-4" y="18" fontSize="10" className="font-mono">
            N
          </text>
        </g>
        <g transform="translate(24 398)">
          <path d="M0,0 H80 M0,-4 V4 M40,-3 V3 M80,-4 V4" className="stroke-fg" strokeWidth="1.25" fill="none" />
          <text x="88" y="4" fontSize="10" className="fill-fg-muted font-mono">
            1 km
          </text>
        </g>
      </g>
      <rect x="0.5" y="0.5" width={W - 1} height={H - 1} rx="6" fill="none" className="stroke-border" />
    </svg>
  );
}
