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
  it("lets an admin choose to publish the whole source despite missing answers", async () => {
    vi.mocked(api.publishBank)
      .mockRejectedValueOnce(new Error("Missing recorded answers for Q10."))
      .mockResolvedValueOnce({ published_count: 111, missing_answer_count: 1 });
    const user = userEvent.setup();
    render(<AdminScreen />);
    await screen.findByText("Gravitation.pdf");
    await user.selectOptions(screen.getByRole("combobox"), "source");
    await user.click(screen.getByRole("button", { name: "Publish questions" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Missing recorded answers for Q10");
    expect(api.publishBank).toHaveBeenNthCalledWith(1, "bank", { source_job_id: "source", question_ids: [], allow_missing_answers: false });

    await user.click(screen.getByRole("checkbox", { name: "Publish even if answers are missing" }));
    await user.click(screen.getByRole("button", { name: "Publish questions" }));
    expect(api.publishBank).toHaveBeenNthCalledWith(2, "bank", { source_job_id: "source", question_ids: [], allow_missing_answers: true });
    expect(await screen.findByRole("status")).toHaveTextContent("Published 111 questions. 1 without recorded answers.");
    expect(screen.getByRole("checkbox", { name: "Publish even if answers are missing" })).not.toBeChecked();
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
