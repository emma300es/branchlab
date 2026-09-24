import type { SVGProps } from "react";
export type IconName = "branch" | "grid" | "activity" | "flask" | "arrow" | "refresh" | "download" | "chevron" | "search" | "check" | "warning" | "file" | "terminal" | "close" | "copy" | "clock" | "external" | "layers" | "shield" | "play";
const paths: Record<IconName, React.ReactNode> = {
  branch: <><circle cx="6" cy="5" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="6" cy="19" r="2.5"/><path d="M6 7.5v9M18 8.5c0 5-12 1-12 7"/></>,
  grid: <><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></>,
  activity: <path d="M2 12h5l3-8 4 16 3-8h5"/>,
  flask: <><path d="M9 3h6M10 3v7L4 20c-.3.6 0 1 1 1h14c1 0 1.3-.4 1-1l-6-10V3M7 15h10"/><path d="M11 17h.01M14 19h.01"/></>,
  arrow: <path d="M4 12h16m-6-6 6 6-6 6"/>,
  refresh: <><path d="M20 7a9 9 0 0 0-15-1L2 9m0-6v6h6M4 17a9 9 0 0 0 15 1l3-3m0 6v-6h-6"/></>,
  download: <><path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/></>,
  chevron: <path d="m9 5 7 7-7 7"/>,
  search: <><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></>,
  check: <path d="m5 12 4 4L19 6"/>,
  warning: <><path d="m12 3 10 18H2L12 3ZM12 9v5M12 17v.1"/></>,
  file: <><path d="M14 2H4v20h16V8l-6-6ZM14 2v6h6M8 13h8M8 17h5"/></>,
  terminal: <><rect x="2" y="4" width="20" height="16" rx="3"/><path d="m6 9 3 3-3 3m6 0h5"/></>,
  close: <path d="m6 6 12 12M6 18 18 6"/>,
  copy: <><rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V3H3v13h5"/></>,
  clock: <><circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/></>,
  external: <><path d="M14 3h7v7m0-7L10 14M10 3H3v18h18v-7"/></>,
  layers: <><path d="m12 3 10 6-10 6L2 9l10-6ZM2 13l10 6 10-6M2 17l10 6 10-6"/></>,
  shield: <><path d="m12 2 9 4v6c0 5-9 10-9 10S3 17 3 12V6l9-4Z"/><path d="m8 12 3 3 5-6"/></>,
  play: <path d="m8 4 12 8-12 8V4Z"/>,
};
export function Icon({ name, ...props }: { name: IconName } & SVGProps<SVGSVGElement>) {
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>{paths[name]}</svg>;
}
