"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useWorkspace } from "@/context/workspace-context";
import { Button, Field, Input, Textarea } from "@/components/ui";
import { Dialog } from "@/components/dialog";
import s from "@/styles/ui.module.css";

export function ReferenceReadyReckoner({ open, onClose, onSubmissionChange }: { open: boolean; onClose: () => void; onSubmissionChange?: (submitting: boolean) => void }) {
  const router = useRouter();
  const { refresh, toast } = useWorkspace();
  const [submitting, setSubmitting] = useState(false);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [fileName, setFileName] = useState<string>("");
  const formId = "reference-generation-form";

  useEffect(() => () => { if (previewUrl) URL.revokeObjectURL(previewUrl); }, [previewUrl]);

  function onFileChange(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) { setPreviewUrl(null); setFileName(""); return; }
    setFileName(file.name);
    if (file.type.startsWith("image/")) {
      const url = URL.createObjectURL(file);
      setPreviewUrl(url);
    } else {
      setPreviewUrl(null);
    }
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    const form = event.currentTarget as HTMLFormElement;
    const fd = new FormData(form);
    const file = fd.get("file") as File | null;
    if (!file || !file.size) {
      toast("Upload an image, PDF or DOCX reference paper.", "error");
      setSubmitting(false);
      return;
    }
    const count = Number(fd.get("desired_count") || 5);
    if (!count || count < 1) {
      toast("Desired count must be at least 1.", "error");
      setSubmitting(false);
      return;
    }
    if (count > 200) {
      toast("Desired count too large (max 200).", "error");
      setSubmitting(false);
      return;
    }
    try {
      // Extraction can take a while. Close the dialog once the submission is
      // underway so the dashboard can show the activity instead.
      onSubmissionChange?.(true);
      onClose();
      const paper = await api.createReferencePaper(fd);
      await refresh();
      toast(`Reference generation queued for "${paper.title}" — ${count} questions. Track in Live activity.`);
      router.push(`/papers/${paper.id}`);
    } catch (caught) {
      toast(caught instanceof Error ? caught.message : "Reference generation failed.", "error");
    } finally {
      setSubmitting(false);
      onSubmissionChange?.(false);
    }
  }

  return (
    <Dialog
      open={open}
      title="Generate from reference"
      subtitle="Upload a picture or short paper — get structural variations instantly. Reuses structural mode & saved to history."
      onClose={onClose}
      footer={<div className={s.modalActions}><Button type="button" onClick={onClose}>Cancel</Button><Button tone="primary" type="submit" form={formId} disabled={submitting}>{submitting ? "Uploading & extracting…" : "Generate"}</Button></div>}
    >
      <div className={s.modalBody}>
        <form id={formId} className={s.formGrid} onSubmit={submit}>
          <Field label="Reference file (image PNG/JPEG/WEBP, PDF, DOCX) — up to 35 MB">
            <input className={s.input} name="file" type="file" accept="image/png,image/jpeg,image/webp,application/pdf,.pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,.docx" required onChange={onFileChange} />
            {fileName && <small className={s.muted}>{fileName}</small>}
            {previewUrl && <img src={previewUrl} alt="preview" style={{ maxWidth: "100%", maxHeight: 200, marginTop: 8, borderRadius: 8, border: "1px solid #e5e7eb" }} />}
          </Field>
          <Field label="Paper title (optional)">
            <Input name="title" placeholder="e.g. JEE Mains — Waves variation" />
          </Field>
          <div className={s.referenceFields}>
            <Field label="Desired number of questions (no cap)">
              <Input name="desired_count" type="number" min={1} max={200} defaultValue={5} required />
            </Field>
            <Field label="Variation strength">
              <select className={s.input} name="variation_strength" defaultValue="balanced">
                <option value="close">Close</option>
                <option value="balanced">Balanced</option>
                <option value="high">High</option>
              </select>
            </Field>
          </div>
          <Field label="Custom instruction">
            <Textarea name="custom_instruction" placeholder="Take this paper as reference & generate a structural variation around this based on the paper. You can also write 'only questions 10-20' here to filter." rows={3} />
          </Field>
          <Field label="Reference questions filter (optional)">
            <Input name="reference_filter" placeholder="e.g. 10-20 or 1,5,10-12 — leave empty for full paper" />
            <small className={s.muted}>If you upload a full paper, list which numbers to use (e.g. 10-20). Also auto-detected if you write “only 10-20” above. Leave empty to use the whole paper.</small>
          </Field>
          <Field label="Exam (optional, auto-detected if blank)">
            <Input name="exam" placeholder="JEE / NEET" />
          </Field>
          <Field label="Subject (optional, auto-detected if blank)">
            <Input name="subject" placeholder="Physics / Mathematics / Chemistry" />
          </Field>
          <p className={s.muted}>Reuses <strong>structural variation</strong> mode. Generates without ingesting to DB first — ready reckoner, saved to paper history for export.</p>
        </form>
      </div>
    </Dialog>
  );
}
