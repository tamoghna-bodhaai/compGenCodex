"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { Icon } from "@/components/icons";
import { LegacyHashRedirect } from "@/components/legacy-hash-redirect";
import { WorkspaceProvider } from "@/context/workspace-context";
import { api } from "@/lib/api";
import type { SessionAccount } from "@/lib/types";
import s from "@/styles/ui.module.css";

const nav = [
  { href: "/", label: "Dashboard", icon: "dashboard" as const },
  { href: "/question-bank", label: "Question bank", icon: "library" as const },
  { href: "/ingestion", label: "Ingestion", icon: "download" as const },
  { href: "/branding", label: "Branding", icon: "branding" as const },
  { href: "/archive", label: "Export archive", icon: "archive" as const },
];

export function Shell({ children, user }: { children: React.ReactNode; user: SessionAccount }) {
  const pathname = usePathname();
  const router = useRouter();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const mobileMenuButton = useRef<HTMLButtonElement>(null);
  const mobileNavigation = useRef<HTMLElement>(null);
  useEffect(() => setMobileNavOpen(false), [pathname]);
  useEffect(() => {
    if (!mobileNavOpen) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const triggerElement = mobileMenuButton.current;
    const navigationElement = mobileNavigation.current;
    const focusable = () => [...(navigationElement?.querySelectorAll<HTMLElement>("a[href], button:not([disabled])") || [])];
    focusable()[0]?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); setMobileNavOpen(false); return; }
      if (event.key !== "Tab") return;
      const items = focusable();
      if (!items.length) return;
      const first = items[0]; const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
      triggerElement?.focus();
    };
  }, [mobileNavOpen]);
  if (pathname === "/login") return <>{children}</>;
  const routeName = pathname.startsWith("/papers/") ? "Paper editor" : pathname.startsWith("/tests") ? "Tests" : pathname.startsWith("/student") ? "My tests" : pathname.startsWith("/admin") ? "Admin" : pathname === "/new-paper" ? "Create paper" : pathname === "/question-bank" ? "Question bank" : pathname === "/ingestion" ? "Ingestion" : pathname === "/branding" ? "Branding" : pathname === "/archive" ? "Export archive" : "Dashboard";
  const navigation = user.role === "student" ? [{ href: "/student", label: "My tests", icon: "document" as const }] : [...nav.filter(item => user.role === "admin" || !["/ingestion", "/archive", "/branding"].includes(item.href)), { href: "/tests", label: "Tests & classes", icon: "document" as const }, ...(user.role === "admin" ? [{ href: "/admin", label: "Admin", icon: "library" as const }] : [])];
  const signOut = async () => {
    try {
      await api.logout();
      router.replace("/login");
      router.refresh();
    } catch {
      // Do not redirect while the server may still consider the cookie valid.
      window.alert("Couldn’t sign out. Please try again.");
    }
  };
  return <div className={s.shell}>
    <aside className={s.sidebar}>
      <Link className={s.brand} href={user.role === "student" ? "/student" : "/"} aria-label="Go to Paper Studio"><span className={s.brandMark}><Icon name="document" /></span><span><strong>Paper Studio</strong><small>JEE workspace</small></span></Link>
      <div className={s.navSection}><span className={s.navLabel}>Workspace</span><nav aria-label="Workspace navigation">{navigation.map((item) => { const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href); return <Link key={item.href} className={`${s.navLink} ${active ? s.navActive : ""}`} href={item.href}><Icon name={item.icon} /><span>{item.label}</span></Link>; })}</nav></div>
      {user.role !== "student" && <Link className={s.sidebarCta} href="/new-paper"><Icon name="plus" />New paper</Link>}
      <div className={s.sidebarSpacer} />
      <div className={s.sidebarFoot}><span>{user.name}</span><small>{user.role === "admin" ? "Super admin" : user.role === "teacher" ? "Teacher" : "Student"}</small></div>
    </aside>
    <main className={s.workspace}>
      <header className={s.topbar}><button ref={mobileMenuButton} className={s.mobileMenuButton} type="button" aria-expanded={mobileNavOpen} aria-controls="mobile-workspace-navigation" onClick={() => setMobileNavOpen((open) => !open)}><Icon name={mobileNavOpen ? "close" : "questions"} /><span className={s.visuallyHidden}>{mobileNavOpen ? "Close navigation" : "Open navigation"}</span></button><div className={s.breadcrumb}><Icon name="document" /><span>Paper Studio</span><Icon name="chevron" /><strong>{routeName}</strong></div><div className={s.topbarActions}>{user.role !== "student" && <Link href="/new-paper" className={s.topbarAction}><Icon name="plus" />New paper</Link>}<button className={s.signOut} type="button" onClick={() => void signOut()}>Sign out</button></div></header>
      {mobileNavOpen && <><button className={s.mobileNavBackdrop} type="button" aria-label="Close navigation" onClick={() => setMobileNavOpen(false)} /><nav ref={mobileNavigation} id="mobile-workspace-navigation" className={s.mobileNav} aria-label="Workspace navigation">{navigation.map((item) => { const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href); return <Link key={item.href} aria-current={active ? "page" : undefined} className={active ? s.mobileActive : ""} href={item.href}><Icon name={item.icon} /><span>{item.label}</span></Link>; })}</nav></>}
      {children}
    </main>
    <LegacyHashRedirect />
  </div>;
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<SessionAccount | null>(null);
  useEffect(() => { if (pathname === "/login") return; let active = true; api.session().then(result => { if (!active) return; if (!result.user) router.replace("/login"); else { setUser(result.user); if (result.user.role === "student" && !pathname.startsWith("/student")) router.replace("/student"); if (result.user.role !== "student" && pathname.startsWith("/student")) router.replace("/"); if (result.user.role === "teacher" && (pathname.startsWith("/admin") || pathname.startsWith("/ingestion") || pathname.startsWith("/archive") || pathname.startsWith("/branding"))) router.replace("/"); } }).catch(() => { if (active) router.replace("/login"); }); return () => { active = false; }; }, [pathname, router]);
  if (pathname === "/login") return <>{children}</>;
  if (!user) return <main style={{ padding: 40 }}>Opening workspace…</main>;
  return <WorkspaceProvider role={user.role}><Shell user={user}>{children}</Shell></WorkspaceProvider>;
}
