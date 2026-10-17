import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Shell } from "@/components/app-shell";

vi.mock("next/navigation", () => ({ usePathname: () => "/", useRouter: () => ({ replace: vi.fn(), refresh: vi.fn() }) }));
vi.mock("@/components/legacy-hash-redirect", () => ({ LegacyHashRedirect: () => null }));
vi.mock("@/components/icons", () => ({ Icon: () => <span aria-hidden="true" /> }));
vi.mock("@/lib/api", () => ({ api: { logout: vi.fn() } }));

afterEach(() => { cleanup(); document.body.style.overflow = ""; });

describe("mobile workspace navigation", () => {
  it("moves focus into the drawer, closes on Escape, and restores focus", async () => {
    const user = userEvent.setup();
    render(<Shell user={{ id: "admin", name: "Admin", email: "admin@example.com", role: "admin", status: "active" }}><p>Workspace</p></Shell>);
    const trigger = screen.getByRole("button", { name: "Open navigation" });

    await user.click(trigger);

    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(document.body.style.overflow).toBe("hidden");
    expect(document.activeElement).toHaveTextContent("Dashboard");

    await user.keyboard("{Escape}");

    expect(screen.getByRole("button", { name: "Open navigation" })).toHaveFocus();
    expect(document.body.style.overflow).toBe("");
  });
});
