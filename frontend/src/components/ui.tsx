"use client";

import type { ButtonHTMLAttributes, InputHTMLAttributes, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";
import { Icon, type IconName } from "@/components/icons";
import type { GenerationJob, IngestionJob, PaperSummary } from "@/lib/types";
import { isActiveGeneration } from "@/lib/logic";
import s from "@/styles/ui.module.css";

export function Button({ tone = "default", size = "default", icon, className = "", children, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { tone?: "default" | "primary" | "quiet" | "danger"; size?: "default" | "small"; icon?: IconName }) {
  return <button type="button" className={`${s.button} ${s[`button_${tone}`]} ${size === "small" ? s.buttonSmall : ""} ${className}`} {...props}>{icon && <Icon name={icon} />}{children}</button>;
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return <label className={s.field}><span className={s.fieldLabel}>{label}</span>{children}{hint && <small>{hint}</small>}</label>;
}
export function Input(props: InputHTMLAttributes<HTMLInputElement>) { return <input className={s.input} {...props} />; }
export function Textarea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) { return <textarea className={s.textarea} {...props} />; }
export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) { return <select className={s.select} {...props} />; }

export function PageHeader({ eyebrow, title, description, actions }: { eyebrow?: string; title: React.ReactNode; description?: React.ReactNode; actions?: React.ReactNode }) {
  return <header className={s.pageHeader}><div>{eyebrow && <div className={s.eyebrow}>{eyebrow}</div>}<h1>{title}</h1>{description && <p>{description}</p>}</div>{actions && <div className={s.pageActions}>{actions}</div>}</header>;
}

export function StatusBadge({ status, children }: { status: string; children: React.ReactNode }) {
  return <span className={`${s.statusBadge} ${s[`status_${status}`] || ""}`}>{children}</span>;
}

export function generationLabel(paper: PaperSummary) {
  const job = paper.generation_job;
  if (!job) return `${paper.question_count} questions · ${paper.status}`;
  if (job.control_state === "cancelled") return `Cancelled · ${job.completed_questions}/${job.total_questions} retained`;
  if (job.control_state === "paused") return `Paused · ${job.completed_questions}/${job.total_questions} complete`;
  if (isActiveGeneration(paper)) return job.operation === "solutions" ? `Solutions · ${job.completed_questions}/${job.total_questions} generated` : `Generating · ${job.completed_questions}/${job.total_questions} validated`;
  if (job.state === "failed") return job.operation === "solutions" ? "Solutions need attention" : "Generation needs attention";
  return `${paper.question_count} questions · ${paper.status}`;
}

export function JobProgress({ job, onPause, onResume, onCancel }: { job: GenerationJob; onPause?: () => void; onResume?: () => void; onCancel?: () => void }) {
  const active = ["queued", "running"].includes(job.state) && job.control_state !== "cancelled";
  const paused = job.control_state === "paused";
  const cancelled = job.control_state === "cancelled";
  const percent = job.total_questions ? Math.round(job.completed_questions / job.total_questions * 100) : 0;
  const subject = job.operation === "solutions" ? "Solution generation" : "Paper generation";
  const title = paused ? `${subject} paused` : cancelled ? `${subject} cancelled` : active ? `${subject} in progress` : job.state === "failed" ? `${subject} needs attention` : `Latest ${subject.toLowerCase()}`;
  return <div className={`${s.jobProgress} ${s[`job_${job.state}`]} ${paused ? s.job_paused : ""}`}>
    <div className={s.jobHead}><div><span className={`${s.liveDot} ${active && !paused ? s.pulse : ""}`} /><strong>{title}</strong></div><span>{job.completed_questions}/{job.total_questions}</span></div>
    <p>{job.message || title}</p>
    {active && job.last_activity_at && <small className={s.muted}>Last activity {new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit", second: "2-digit" }).format(new Date(job.last_activity_at))}</small>}
    {active && <div className={s.progressTrack} aria-label={`${percent}% complete`}><span style={{ width: `${percent}%` }} /></div>}
    {active && (onPause || onResume || onCancel) && <div className={s.inlineActions}>{paused ? <Button size="small" tone="primary" onClick={onResume}>Resume</Button> : <Button size="small" onClick={onPause}>Pause</Button>}<Button size="small" tone="danger" onClick={onCancel}>Cancel</Button></div>}
    {job.error_message && <p className={s.errorText}>{job.error_message}</p>}
  </div>;
}

export function IngestionProgress({ job, onPause, onResume, onCancel }: { job: IngestionJob; onPause?: () => void; onResume?: () => void; onCancel?: () => void }) {
  const paused = job.control_state === "paused";
  const cancelled = job.control_state === "cancelled";
  const active = ["queued", "running"].includes(job.state) && !cancelled;
  const total = job.total_chunks || 0;
  const completed = Math.min(job.completed_chunks || 0, total);
  const percent = total ? Math.round(completed / total * 100) : 0;
  const title = paused ? "Question ingestion paused" : cancelled ? "Question ingestion cancelled" : active ? "Question ingestion in progress" : job.result_status === "partial" ? "Question ingestion partially complete" : job.state === "failed" ? "Question ingestion needs attention" : "Latest question ingestion";
  const progressLabel = total ? `${completed} of ${total} source chunks classified` : "Preparing source chunks";
  const progressStatus = job.state === "failed" ? "Import failed" : `${percent}% complete`;
  return <div className={`${s.ingestionProgress} ${s[`job_${job.state}`]}`}>
    <div className={s.ingestionSummary}>
      <div className={s.ingestionStatus}><span className={`${s.liveDot} ${active ? s.pulse : ""}`} /><div><div className={s.ingestionTitle}><strong>{title}</strong>{total > 0 && <span className={`${s.ingestionPercent} ${job.state === "failed" ? s.ingestionPercentFailed : ""}`}>{progressStatus}</span>}</div><span>{job.message || job.phase || "Preparing your source"}</span></div></div>
    </div>
    {active && !paused && <div className={s.ingestionTrack} role="progressbar" aria-label={progressLabel} aria-valuemin={0} aria-valuemax={total || undefined} aria-valuenow={total ? completed : undefined}><span style={{ width: `${percent}%` }} /></div>}
    <div className={s.ingestionMeta}><span>{total ? progressLabel : "Organizing the source for classification"}</span>{job.accepted_questions ? <strong>{job.accepted_questions} accepted</strong> : job.ingested_questions ? <strong>{job.ingested_questions} questions found</strong> : null}</div>
    {(job.review_questions || job.skipped_chunks) ? <div className={s.ingestionMeta}><span>{job.review_questions || 0} need review · {job.skipped_chunks || 0} chunks skipped</span></div> : null}
    {active && (onPause || onResume || onCancel) ? <div className={s.inlineActions}>{paused ? <Button size="small" tone="primary" onClick={onResume}>Resume</Button> : <Button size="small" onClick={onPause}>Pause</Button>}<Button size="small" tone="danger" onClick={onCancel}>Cancel</Button></div> : null}
    {job.error_message && <div className={s.ingestionError} role="alert"><strong>Import error</strong><span>{job.error_message}</span></div>}
  </div>;
}

export function EmptyState({ icon = "document", title, description, action }: { icon?: IconName; title: string; description: string; action?: React.ReactNode }) {
  return <div className={s.empty}><span className={s.emptyIcon}><Icon name={icon} /></span><h2>{title}</h2><p>{description}</p>{action}</div>;
}
