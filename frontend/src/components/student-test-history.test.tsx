import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { StudentTestsScreen } from "@/components/test-engine-screen";
import { api } from "@/lib/api";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/components/math-content", () => ({ MathContent: ({ value }: { value: string }) => <span>{value}</span> }));
vi.mock("@/lib/api", () => ({ api: { studentTests: vi.fn(), startAttempt: vi.fn() } }));

afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("student test history", () => {
  it("keeps a submitted result visible while a later attempt is active", async () => {
    const submitted = { id: "submitted-1", test_id: "test-1", started_at: "2026-01-01T09:00:00Z", ends_at: "2026-01-01T10:00:00Z", submitted_at: "2026-01-01T09:30:00Z", score: 42, revision: 2 };
    const active = { id: "active-2", test_id: "test-1", started_at: "2026-01-02T09:00:00Z", ends_at: "2026-01-02T10:00:00Z", submitted_at: null, score: null, revision: 0 };
    vi.mocked(api.studentTests).mockResolvedValue({ items: [{ id: "test-1", paper_id: "paper-1", title: "JEE mock", status: "published", duration_seconds: 10_800, opens_at: null, closes_at: null, max_attempts: 3, results_policy: { mode: "immediate" }, question_count: 75, version: 1, assignment_count: 1, availability: "available", attempts_remaining: 1, attempts: [active, submitted], active_attempt: active, latest_submitted_attempt: submitted, latest_attempt: active }] });

    render(<StudentTestsScreen />);

    expect(await screen.findByRole("link", { name: "Resume attempt" })).toHaveAttribute("href", "/student/attempts/active-2");
    expect(screen.getByRole("link", { name: "View result · 42 points" })).toHaveAttribute("href", "/student/results/submitted-1");
    expect(screen.getByRole("heading", { name: "Attempt history" })).toBeInTheDocument();
  });
});
