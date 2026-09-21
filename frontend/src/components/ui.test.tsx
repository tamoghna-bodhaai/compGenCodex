import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Button, IngestionProgress, JobProgress } from "@/components/ui";
import { Dialog } from "@/components/dialog";

describe("Button", () => {
  it("does not submit a form unless submit is requested", async () => {
    const submit = vi.fn((event: React.FormEvent) => event.preventDefault());
    const user = userEvent.setup();
    render(<form onSubmit={submit}><Button>Quiet action</Button><Button type="submit">Save</Button></form>);
    await user.click(screen.getByRole("button", { name: "Quiet action" }));
    expect(submit).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(submit).toHaveBeenCalledOnce();
  });
});

describe("Dialog", () => {
  it("keeps closed dialogs closed and requests close only once", async () => {
    const close = vi.fn();
    const user = userEvent.setup();
    const { rerender } = render(<Dialog open={false} title="Editor" onClose={close}><p>Contents</p></Dialog>);
    expect(screen.queryByRole("dialog", { hidden: true })).not.toBeInTheDocument();

    rerender(<Dialog open title="Editor" onClose={close}><p>Contents</p></Dialog>);
    expect(screen.getByRole("dialog")).toHaveAttribute("open");
    await user.click(screen.getByRole("button", { name: "Close dialog" }));
    expect(close).toHaveBeenCalledOnce();

    rerender(<Dialog open={false} title="Editor" onClose={close}><p>Contents</p></Dialog>);
    expect(screen.queryByRole("dialog", { hidden: true })).not.toBeInTheDocument();
    expect(close).toHaveBeenCalledOnce();
  });

  it("routes Escape through the controlled close callback", () => {
    const close = vi.fn();
    render(<Dialog open title="Editor" onClose={close}><p>Contents</p></Dialog>);
    const dialog = screen.getByRole("dialog");
    dialog.dispatchEvent(new Event("cancel", { bubbles: false, cancelable: true }));
    expect(close).toHaveBeenCalledOnce();
  });

  it("mounts only the active dialog when several controlled dialogs exist", () => {
    const { container } = render(<><Dialog open title="Active" onClose={() => undefined}>Active body</Dialog><Dialog open={false} title="Inactive" onClose={() => undefined}>Inactive body</Dialog></>);
    expect(within(container).getAllByRole("dialog")).toHaveLength(1);
    expect(within(container).queryByText("Inactive body")).not.toBeInTheDocument();
  });
});

describe("JobProgress", () => {
  it("exposes resume and cancel controls for paused generation", async () => {
    const resume = vi.fn(); const cancel = vi.fn(); const user = userEvent.setup();
    render(<JobProgress job={{ id: "j", paper_id: "p", operation: "initial", state: "running", control_state: "paused", total_questions: 10, completed_questions: 4, message: "Paused" }} onResume={resume} onCancel={cancel} />);
    await user.click(screen.getByRole("button", { name: "Resume" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(resume).toHaveBeenCalledOnce();
    expect(cancel).toHaveBeenCalledOnce();
    expect(screen.getByLabelText("40% complete")).toBeInTheDocument();
  });
});

describe("IngestionProgress", () => {
  it("connects the chunk count, percentage, and accessible progress value", () => {
    render(<IngestionProgress job={{ id: "i", source_name: "waves.docx", state: "running", phase: "Classifying", message: "Classifying 2 of 4 source chunks", total_chunks: 4, completed_chunks: 2, ingested_questions: 5 }} />);
    expect(screen.getByText("50% complete")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "2 of 4 source chunks classified" })).toHaveAttribute("aria-valuenow", "2");
    expect(screen.getByText("5 questions found")).toBeInTheDocument();
  });

  it("presents a failed import as a contained error state", () => {
    render(<IngestionProgress job={{ id: "failed", source_name: "waves.docx", state: "failed", phase: "Ingestion needs attention", total_chunks: 4, completed_chunks: 0, error_message: "The classification response was incomplete." }} />);
    expect(screen.getByText("Import failed")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Import errorThe classification response was incomplete.");
  });
});
