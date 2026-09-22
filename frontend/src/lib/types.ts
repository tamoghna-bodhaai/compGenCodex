export type JobState = "queued" | "running" | "succeeded" | "failed";
export type ControlState = "active" | "paused" | "cancelled";
export type PaperStatus = "draft" | "generated" | "final";
export type QuestionType = "single_correct_mcq" | "multiple_correct_mcq" | "numerical" | "subjective";
export type GenerationMode = "structural_variation" | "concept_variation";
export type VariationStrength = "close" | "balanced" | "high";

export interface GenerationJob {
  id: string;
  paper_id: string;
  operation: "initial" | "solutions";
  state: JobState;
  control_state?: ControlState;
  total_questions: number;
  completed_questions: number;
  message?: string;
  error_message?: string;
  last_activity_at?: string;
}

export interface IngestionJob {
  id: string;
  source_name: string;
  state: JobState;
  phase: string;
  message?: string;
  total_chunks?: number;
  completed_chunks?: number;
  ingested_questions?: number;
  error_message?: string;
  control_state?: ControlState;
  result_status?: "pending" | "succeeded" | "partial" | "cancelled";
  accepted_questions?: number;
  review_questions?: number;
  skipped_chunks?: number;
  retryable_chunks?: number;
}

export interface QuestionJson {
  stem: string;
  options?: string[];
  marks?: number;
  primary_concept?: string | null;
  secondary_concepts?: string[];
  estimated_time_minutes?: number | null;
}

export interface PaperQuestion {
  id: string;
  paper_id: string;
  section_id: string | null;
  position: number;
  question_json: QuestionJson;
  answer_json: { correct_answer?: string | null } | null;
  solution: string | null;
  question_type: QuestionType;
  difficulty: number;
  locked: boolean;
  generation_metadata?: Record<string, unknown> | null;
  updated_at?: string;
}

export interface PaperSection {
  id: string;
  paper_id: string;
  title: string;
  position: number;
}

export interface BrandingConfig {
  institution_name?: string;
  address?: string;
  contact?: string;
  logo_data_url?: string | null;
  duration_minutes?: number | null;
  total_marks?: number | null;
  watermark_text?: string;
  header_text?: string;
  footer_text?: string;
  instructions?: string[];
  layout?: BrandingLayout;
}

export interface BrandingLayout {
  preset?: "coaching" | "academic" | "watermarked" | "custom";
  header_enabled?: boolean;
  footer_enabled?: boolean;
  header_left?: string;
  header_center?: string;
  header_right?: string;
  footer_left?: string;
  footer_center?: string;
  footer_right?: string;
  logo_position?: "left" | "center" | "right";
  logo_size?: number;
  divider_enabled?: boolean;
  divider_color?: string;
  page_number_position?: "left" | "center" | "right";
  font_size?: number;
  watermark_enabled?: boolean;
  watermark_opacity?: number;
  watermark_size?: number;
  watermark_rotation?: number;
}

export interface SubtopicPlanPayload {
  topic: string;
  subtopic?: string;
  chapters: string[];
  section_title: string;
  question_types: Array<{ type: QuestionType; count: number }>;
  difficulty_distribution: Array<{ difficulty: number; count: number }>;
  generation_mode: GenerationMode;
  variation_strength: VariationStrength;
  seed_question_ids?: string[];
}

export interface GenerationConfig {
  title: string;
  exam: string;
  subject: string;
  chapters: string[];
  topics: string[];
  subtopics: string[];
  concepts: string[];
  question_types: Array<{ type: QuestionType; count: number }>;
  difficulty_distribution: Array<{ difficulty: number; count: number }>;
  generation_mode: GenerationMode;
  variation_strength: VariationStrength;
  subtopic_plans?: SubtopicPlanPayload[];
  reference_questions?: ReferenceSnapshot[];
  reference_filter_formatted?: string;
}

export interface PaperSummary {
  id: string;
  title: string;
  exam: string;
  subject: string;
  status: PaperStatus;
  created_at: string;
  updated_at: string;
  question_count: number;
  requested_question_count: number;
  generation_job: GenerationJob | null;
}

export interface Paper extends PaperSummary {
  generation_config: GenerationConfig;
  branding_config: BrandingConfig;
  branding_template_id?: string | null;
  sections: PaperSection[];
  questions: PaperQuestion[];
}

export interface CatalogRow {
  exam: string;
  subject: string;
  chapter: string | null;
  topic: string | null;
  subtopic: string | null;
  question_type: QuestionType;
  difficulty: number;
  count: number;
}

export interface SeedQuestion {
  id: string;
  source_key?: string;
  source_reference?: string | null;
  source?: string | null;
  exam: string;
  subject: string;
  chapter?: string | null;
  topic?: string | null;
  subtopic?: string | null;
  primary_concept?: string | null;
  secondary_concepts?: string[];
  question_archetype?: string | null;
  expected_time_minutes?: number | null;
  marks?: number | null;
  question_type: QuestionType;
  difficulty: number;
  verification_status: string;
  question_json: QuestionJson;
  answer_json?: { correct_answer?: string | null } | null;
}

export interface SeedComparison {
  comparison_mode?: "seed_bank" | "reference";
  question: PaperQuestion;
  generation_metadata: Record<string, unknown>;
  seeds: SeedQuestion[];
  missing_seed_question_ids: string[];
  reference?: ReferenceSnapshot | null;
  reference_mapping?: ReferenceMapping | null;
}

export interface ReferenceSnapshot {
  reference_question_id?: string;
  reference_selection_index?: number;
  reference_source_position?: number;
  source_question_number?: number | null;
  exam?: string | null;
  subject?: string | null;
  chapter?: string | null;
  topic?: string | null;
  subtopic?: string | null;
  question_type: QuestionType;
  difficulty: number;
  stem: string;
  options?: string[];
  correct_answer?: string | null;
  solution?: string | null;
  primary_concept?: string | null;
  question_archetype?: string | null;
}

export interface ReferenceMapping {
  reference_question_id?: string;
  reference_question_index?: number;
  source_question_number?: number | null;
  reference_reused?: boolean;
}

export interface PaperComparisonItem {
  generated_question: PaperQuestion;
  generated_position: number;
  reference: ReferenceSnapshot | null;
  reference_mapping: ReferenceMapping | null;
  mapping_status: "matched" | "reused" | "unavailable";
}

export interface PaperComparison {
  comparison_mode: "reference";
  paper_id: string;
  title: string;
  reference_filter: string | null;
  reference_count: number;
  generated_count: number;
  items: PaperComparisonItem[];
}

export interface BrandingProfile {
  id: string;
  name: string;
  branding_config: BrandingConfig;
}

export interface PaperExport {
  id: string;
  paper_id: string | null;
  kind: "paper" | "legacy";
  filename: string;
  relative_path: string;
  media_type: string;
  byte_size: number;
  sha256: string;
  created_at: string;
}

export interface CreationPlan {
  questionCounts: Record<QuestionType, number>;
  difficultyCounts: Record<"easy" | "medium" | "hard", number>;
  generation_mode: GenerationMode;
  variation_strength: VariationStrength;
  sectionTitle: string;
  seedQuestionIds: string[];
}

export interface CreationState {
  title: string;
  exam: string;
  subject: string;
  chapters: string[];
  subtopicKeys: string[];
  plans: Record<string, CreationPlan>;
  generation_mode: GenerationMode;
  variation_strength: VariationStrength;
  total_marks: number;
  duration_minutes: number;
}

export interface QuestionDraft {
  stem: string;
  options: string[];
  correct_answer: string;
  difficulty: number;
  marks: number;
  solution: string;
  section_id: string;
  custom_instruction: string;
}

export interface BankSelection {
  exam?: string;
  subject?: string;
  chapter?: string;
  topic?: string;
  subtopic?: string;
}
