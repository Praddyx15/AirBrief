import { cn } from "@/lib/utils";

export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <svg
        viewBox="0 0 24 24"
        width="22"
        height="22"
        aria-hidden
        focusable="false"
        className="shrink-0 text-accent"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <circle cx="12" cy="12" r="9.25" />
        <path d="M12 2.75v3M12 18.25v3M2.75 12h3M18.25 12h3" />
        <path d="M12 7.5l2.25 6.5L12 12.75 9.75 14z" fill="currentColor" />
      </svg>
      <span className="text-[15px] font-semibold tracking-tight text-fg">
        Air<span className="text-accent-text">Brief</span>
      </span>
    </span>
  );
}
