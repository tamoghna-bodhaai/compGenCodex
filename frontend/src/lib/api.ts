import type {
  BrandingProfile,
  CatalogRow,
  IngestionJob,
  Paper,
  PaperSummary,
  SeedQuestion,
  SeedComparison,
  PaperExport,
  PaperComparison,
} from "@/lib/types";

export class ApiError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message);
    this.name = "ApiError";
  }
}

function errorDetailMessage(detail: unknown): string | null {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => {
      if (!item || typeof item !== "object") return null;
      const issue = item as { loc?: unknown; msg?: unknown };
      const location = Array.isArray(issue.loc) ? issue.loc.filter((part) => part !== "body").join(".") : "";
      const message = typeof issue.msg === "string" ? issue.msg : null;
      return message ? `${location ? `${location}: ` : ""}${message}` : null;
    }).filter((message): message is string => Boolean(message));
    return messages.length ? messages.join("; ") : null;
  }
  return null;
}

export async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const isForm = init.body instanceof FormData;
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: {
      ...(isForm ? {} : init.body ? { "Content-Type": "application/json" } : {}),
      ...(init.headers || {}),
    },
  });
  if (!response.ok) {
    let message = "Something went wrong.";
    try {
      const body = (await response.json()) as { detail?: unknown };
      message = errorDetailMessage(body.detail) || message;
    } catch {
      // The API sometimes returns an empty body for transport failures.
    }
    throw new ApiError(message, response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  login: (email: string, accessCode: string) => apiRequest<{ authenticated: boolean; user: { email: string } }>("/auth/login", { method: "POST", body: JSON.stringify({ email, access_code: accessCode }) }),
  logout: () => apiRequest<void>("/auth/logout", { method: "POST" }),
  session: () => apiRequest<{ authenticated: boolean; user: { email: string } | null }>("/auth/session"),
  papers: (signal?: AbortSignal) => apiRequest<{ items: PaperSummary[] }>("/papers", { signal }),
  paper: (id: string, signal?: AbortSignal) => apiRequest<Paper>(`/papers/${id}`, { signal }),
  paperQuestionSeeds: (paperId: string, questionId: string, signal?: AbortSignal) => apiRequest<SeedComparison>(`/papers/${paperId}/questions/${questionId}/seeds`, { signal }),
  paperComparison: (paperId: string, signal?: AbortSignal) => apiRequest<PaperComparison>(`/papers/${paperId}/comparison`, { signal }),
  createPaper: (body: unknown) => apiRequest<Paper>("/papers", { method: "POST", body: JSON.stringify(body) }),
  updatePaper: (id: string, body: unknown) => apiRequest<Paper>(`/papers/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deletePaper: (id: string) => apiRequest<void>(`/papers/${id}`, { method: "DELETE" }),
  catalog: (signal?: AbortSignal) => apiRequest<{ items: CatalogRow[] }>("/questions/catalog", { signal }),
  ingestionJobs: (signal?: AbortSignal) => apiRequest<{ items: IngestionJob[] }>("/questions/ingestion-jobs", { signal }),
  deleteIngestionJob: (id: string) => apiRequest<void>(`/questions/ingestion-jobs/${id}`, { method: "DELETE" }),
  pauseIngestion: (id: string) => apiRequest<{ job: IngestionJob }>(`/questions/ingestion-jobs/${id}/pause`, { method: "POST" }),
  resumeIngestion: (id: string) => apiRequest<{ job: IngestionJob }>(`/questions/ingestion-jobs/${id}/resume`, { method: "POST" }),
  cancelIngestion: (id: string) => apiRequest<{ job: IngestionJob }>(`/questions/ingestion-jobs/${id}/cancel`, { method: "POST" }),
  ingestionReport: (id: string) => apiRequest<{ summary: { accepted: number; review_needed: number; skipped_chunks: number; retryable_chunks: number } }>(`/questions/ingestion-jobs/${id}/report`),
  ingest: (body: FormData) => apiRequest<{ job: IngestionJob }>("/questions/ingest", { method: "POST", body }),
  createReferencePaper: (body: FormData) => apiRequest<Paper>("/papers/from-reference", { method: "POST", body }),
  questions: (query: URLSearchParams, signal?: AbortSignal) => apiRequest<{ items: SeedQuestion[]; total: number; offset: number }>(`/questions?${query}`, { signal }),
  question: (id: string, signal?: AbortSignal) => apiRequest<SeedQuestion>(`/questions/${id}`, { signal }),
  brandingProfiles: (signal?: AbortSignal) => apiRequest<{ items: BrandingProfile[] }>("/branding-profiles", { signal }),
  saveBrandingProfile: (body: unknown) => apiRequest<BrandingProfile>("/branding-profiles", { method: "POST", body: JSON.stringify(body) }),
  updateBrandingProfile: (id: string, body: unknown) => apiRequest<BrandingProfile>(`/branding-profiles/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  duplicateBrandingProfile: (id: string) => apiRequest<BrandingProfile>(`/branding-profiles/${id}/duplicate`, { method: "POST" }),
  deleteBrandingProfile: (id: string) => apiRequest<void>(`/branding-profiles/${id}`, { method: "DELETE" }),
  exports: (kind?: "paper" | "legacy", signal?: AbortSignal) => apiRequest<{ items: PaperExport[] }>(`/exports${kind ? `?kind=${kind}` : ""}`, { signal }),
  paperExports: (paperId: string, signal?: AbortSignal) => apiRequest<{ items: PaperExport[] }>(`/exports?paper_id=${encodeURIComponent(paperId)}`, { signal }),
  post: <T>(path: string, body?: unknown) => apiRequest<T>(path, { method: "POST", ...(body === undefined ? {} : { body: JSON.stringify(body) }) }),
  put: <T>(path: string, body: unknown) => apiRequest<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  delete: <T>(path: string) => apiRequest<T>(path, { method: "DELETE" }),
};

export async function downloadPaper(paper: Paper | PaperSummary, format: "pdf" | "docx", variant: "question_paper" | "answer_key", brandingTemplateId?: string | null, brandingOverrides?: { total_marks?: number | null; duration_minutes?: number | null }) {
  const response = await fetch(`/api/papers/${paper.id}/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ format, variant, ...(brandingTemplateId ? { branding_template_id: brandingTemplateId } : {}), ...(brandingOverrides ? { branding_overrides: brandingOverrides } : {}) }),
  });
  if (!response.ok) {
    let message = "Export failed.";
    try { message = ((await response.json()) as { detail?: string }).detail || message; } catch { /* empty response */ }
    throw new ApiError(message, response.status);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  const variantSuffix = variant === "answer_key" ? "answerkey" : "question";
  const maxTitleLength = 120 - variantSuffix.length - 1;
  const safeTitle = paper.title.replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^_+|_+$/g, "").slice(0, maxTitleLength) || "question_paper";
  anchor.download = `${safeTitle}_${variantSuffix}.${format}`;
  anchor.click();
  URL.revokeObjectURL(url);
}

export function downloadArchivedExport(item: PaperExport) {
  const anchor = document.createElement("a");
  anchor.href = `/api/exports/${item.id}/download`;
  anchor.download = item.filename;
  anchor.click();
}
