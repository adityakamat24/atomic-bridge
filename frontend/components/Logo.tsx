"use client";

/**
 * Atomicwork-inspired mark: rounded square with a radial purple gradient,
 * containing a stylized "bridge" glyph — two arcs meeting at a node.
 *
 * Distinct from Atomicwork's actual atom mark (no trademark conflict)
 * but shares the same visual language: gradient + rounded square + white
 * geometric glyph.
 */
export function BrandMark({
  size = 32,
  className = "",
}: {
  size?: number;
  className?: string;
}) {
  const id = "bm-grad";
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-label="Atomic Bridge"
    >
      <rect width="32" height="32" rx="8" fill={`url(#${id})`} />
      {/* Bridge: two arcs meeting at a central node */}
      <path
        d="M6.5 21 C 6.5 14.5, 11 11, 16 11 C 21 11, 25.5 14.5, 25.5 21"
        stroke="white"
        strokeWidth="2.4"
        strokeLinecap="round"
        fill="none"
      />
      <circle cx="16" cy="11" r="2.4" fill="white" />
      <line
        x1="6.5"
        y1="22"
        x2="25.5"
        y2="22"
        stroke="white"
        strokeWidth="1.6"
        strokeLinecap="round"
        opacity="0.7"
      />
      <defs>
        <radialGradient
          id={id}
          cx="0"
          cy="0"
          r="1"
          gradientUnits="userSpaceOnUse"
          gradientTransform="translate(2 4) rotate(45) scale(40)"
        >
          <stop stopColor="#9966FF" />
          <stop offset="0.6" stopColor="#7C3AED" />
          <stop offset="1" stopColor="#4F1F72" />
        </radialGradient>
      </defs>
    </svg>
  );
}
