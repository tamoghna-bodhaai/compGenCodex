"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Icon } from "@/components/icons";
import { LegacyHashRedirect } from "@/components/legacy-hash-redirect";
import { WorkspaceProvider } from "@/context/workspace-context";
import { api } from "@/lib/api";
import s from "@/styles/ui.module.css";

const nav = [
  { href: "/", label: "Dashboard", icon: "dashboard" as const },
  { href: "/question-bank", label: "Question bank", icon: "library" as const },
  { href: "/branding", label: "Branding", icon: "branding" as const },
  { href: "/archive", label: "Export archive", icon: "archive" as const },
];

function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  if (pathname === "/login") return <>{children}</>;
  const routeName = pathname.startsWith("/papers/") ? "Paper editor" : pathname === "/new-paper" ? "Create paper" : pathname === "/question-bank" ? "Question bank" : pathname === "/branding" ? "Branding" : pathname === "/archive" ? "Export archive" : "Dashboard";
  const signOut = async () => {
    await api.logout();
    router.replace("/login");
    router.refresh();
  };
  return <div className={s.shell}>
    <aside className={s.sidebar}>
      <Link className={s.brand} href="/" aria-label="Go to Paper Studio dashboard"><span className={s.brandMark}><Icon name="document" /></span><span><strong>Paper Studio</strong><small>JEE workspace</small></span></Link>
      <div className={s.navSection}><span className={s.navLabel}>Workspace</span><nav aria-label="Workspace navigation">{nav.map((item) => { const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href); return <Link key={item.href} className={`${s.navLink} ${active ? s.navActive : ""}`} href={item.href}><Icon name={item.icon} /><span>{item.label}</span></Link>; })}</nav></div>
      <Link className={s.sidebarCta} href="/new-paper"><Icon name="plus" />New paper</Link>
      <div className={s.sidebarSpacer} />
      <div className={s.sidebarFoot}><span>Question Paper Generator</span><small>Focused tools for educators</small></div>
    </aside>
    <main className={s.workspace}>
      <header className={s.topbar}><div className={s.breadcrumb}><Icon name="document" /><span>Paper Studio</span><Icon name="chevron" /><strong>{routeName}</strong></div><div className={s.topbarActions}><Link href="/new-paper" className={s.topbarAction}><Icon name="plus" />New paper</Link><button className={s.signOut} type="button" onClick={() => void signOut()}>Sign out</button></div></header>
      <nav className={s.mobileNav} aria-label="Workspace navigation">{nav.map((item) => <Link key={item.href} className={(item.href === "/" ? pathname === "/" : pathname.startsWith(item.href)) ? s.mobileActive : ""} href={item.href}><Icon name={item.icon} /><span>{item.label}</span></Link>)}<Link className={pathname === "/new-paper" ? s.mobileActive : ""} href="/new-paper"><Icon name="plus" /><span>New paper</span></Link></nav>
      {children}
    </main>
    <LegacyHashRedirect />
  </div>;
}

export function AppShell({ children }: { children: React.ReactNode }) {
  return <WorkspaceProvider><Shell>{children}</Shell></WorkspaceProvider>;
}
