import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AdminScreen } from "@/components/test-engine-screen";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {},
  api: {
    teachers: vi.fn().mockResolvedValue({ items: [] }),
    banks: vi.fn().mockResolvedValue({ items: [{ id: "bank", name: "Physics", status: "draft", question_count: 0 }] }),
    ingestionJobs: vi.fn().mockResolvedValue({ items: [{ id: "source", source_name: "Gravitation.pdf", state: "succeeded" }] }),
    publishBank: vi.fn(),
  },
}));

afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("bank publication", () => {
  it("keeps review failures visible and does not offer an answer bypass", async () => {
    vi.mocked(api.publishBank).mockRejectedValueOnce(new Error("Every question must complete source review before publication."));
    const user = userEvent.setup();
    render(<AdminScreen />);
    await screen.findByText("Gravitation.pdf");
    await user.selectOptions(screen.getByRole("combobox"), "source");
    await user.click(screen.getByRole("button", { name: "Publish questions" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("complete source review");
    expect(api.publishBank).toHaveBeenCalledWith("bank", { source_job_id: "source", question_ids: [] });
    expect(screen.queryByRole("checkbox", { name: "Publish even if answers are missing" })).not.toBeInTheDocument();
  });

  it("shows source-level publication tracking and refreshes current counts", async () => {
    const bank = { id: "bank", name: "Physics", status: "published", question_count: 2, missing_answer_count: 1, missing_solution_count: 1, sources: [{ source_job_id: "source", source_name: "Gravitation.pdf", question_count: 2, source_question_count: 3, missing_answer_count: 1, missing_solution_count: 1 }] };
    vi.mocked(api.banks).mockResolvedValueOnce({ items: [bank] }).mockResolvedValueOnce({ items: [{ ...bank, missing_answer_count: 0, sources: [{ ...bank.sources[0], missing_answer_count: 0 }] }] });
    const user = userEvent.setup();
    render(<AdminScreen />);
    const summary = await screen.findByLabelText("Physics publication summary");
    expect(within(summary).getByText("missing answers")).toHaveTextContent("1 missing answers");
    const source = screen.getByRole("link", { name: "Gravitation.pdf" });
    expect(source).toHaveAttribute("href", "/ingestion/source");
    expect(source.parentElement?.parentElement).toHaveTextContent("2 of 3 in this bank · 1 missing answers · 1 missing solutions");
    await user.click(screen.getByRole("button", { name: "Refresh tracking" }));
    expect(within(summary).getByText("missing answers")).toHaveTextContent("0 missing answers");
  });
});
