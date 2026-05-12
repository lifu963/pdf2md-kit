export type JobStatus = "idle" | "extracting" | "extracted" | "building" | "ready" | "failed";

export type PageStatus = "pending" | "extracting" | "done" | "failed";

export interface JobResponse {
  job_id: string;
  status: JobStatus;
  total_pages: number;
  succeeded_pages: number[];
  failed_pages: number[];
  processed_count: number;
}

export interface CreateJobResponse {
  job_id: string;
  total_pages: number;
  status: JobStatus;
}

export interface PageSummaryResponse {
  page_num: number;
  status: PageStatus;
}

export interface PageResponse {
  page_num: number;
  status: PageStatus;
  content?: string;
  error?: string;
}

export interface RetryPageAcceptedResponse {
  job_id: string;
  page_num: number;
}

export type BuildMergeMode = "direct" | "separator" | "separator_with_page_number";

export interface BuildResponse {
  status: JobStatus;
  output_url: string;
  download_url: string;
}

export interface BuildOutputRequest {
  merge_mode: BuildMergeMode;
}

export interface OutputDocumentResponse {
  content: string;
  updated_at: string;
}

export interface ConfigModelPayload {
  name: string;
  timeout: number;
}

export interface ExtractConfigPayload {
  dpi: number;
  concurrency: number;
  max_retries: number;
  prompt: string;
}

export interface PublicConfigResponse {
  model: ConfigModelPayload;
  extract: ExtractConfigPayload;
  has_api_key: boolean;
}

export interface UpdateConfigRequest {
  model: ConfigModelPayload;
  extract: ExtractConfigPayload;
  api_key?: string;
}

export interface TestConnectionResponse {
  ok: boolean;
  message: string;
  reply_preview?: string;
}

export interface SinglePagePreviewResponse {
  page_num: number;
  content: string;
}

export interface ApiErrorDetail {
  code: string;
  message: string;
  details?: Record<string, unknown> | null;
}

export interface ApiErrorResponse {
  detail: ApiErrorDetail;
}

export interface PageEventPayload {
  type: "page";
  page_num: number;
  status: PageStatus;
  processed_count: number;
  total_pages: number;
  error?: string;
}

export interface CompleteEventPayload {
  type: "complete";
  processed_count: number;
  total_pages: number;
  succeeded_pages: number[];
  failed_pages: number[];
}

export interface FailedEventPayload {
  type: "failed";
  detail: string;
}

export type JobStreamEvent = PageEventPayload | CompleteEventPayload | FailedEventPayload;
