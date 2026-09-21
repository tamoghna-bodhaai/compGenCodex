import type { SVGProps } from "react";

export type IconName = "dashboard" | "library" | "plus" | "search" | "document" | "sparkle" | "check" | "questions" | "arrow" | "download" | "close" | "chevron" | "lock" | "branding" | "archive";

export function Icon({ name, ...props }: SVGProps<SVGSVGElement> & { name: IconName }) {
  const paths: Record<IconName, React.ReactNode> = {
    dashboard: <><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></>,
    library: <><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z"/></>,
    plus: <path d="M12 5v14M5 12h14"/>, search: <><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></>,
    document: <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6M8 13h8M8 17h6"/></>,
    sparkle: <><path d="m12 3-1.1 3.1a3 3 0 0 1-1.8 1.8L6 9l3.1 1.1a3 3 0 0 1 1.8 1.8L12 15l1.1-3.1a3 3 0 0 1 1.8-1.8L18 9l-3.1-1.1a3 3 0 0 1-1.8-1.8L12 3Z"/><path d="m5 16-.5 1.5A2 2 0 0 1 3 19l1.5.5A2 2 0 0 1 6 21l.5-1.5A2 2 0 0 1 8 18l-1.5-.5A2 2 0 0 1 5 16Z"/></>,
    check: <path d="M20 6 9 17l-5-5"/>, questions: <><path d="M8 6h13M8 12h13M8 18h13"/><path d="M3 6h.01M3 12h.01M3 18h.01"/></>,
    arrow: <path d="M5 12h14M13 6l6 6-6 6"/>, download: <><path d="M12 3v12m0 0 4-4m-4 4-4-4"/><path d="M5 21h14"/></>,
    close: <path d="m6 6 12 12M18 6 6 18"/>, chevron: <path d="m9 18 6-6-6-6"/>,
    lock: <><rect width="16" height="11" x="4" y="11" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></>,
    branding: <><path d="M4 20h16M6 17V8m6 9V4m6 13v-6"/><circle cx="6" cy="6" r="2"/><circle cx="12" cy="2.5" r="2"/><circle cx="18" cy="9" r="2"/></>,
    archive: <><path d="M4 7h16v13H4zM3 4h18v3H3z"/><path d="M9 12h6"/></>,
  };
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...props}>{paths[name]}</svg>;
}
