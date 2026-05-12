import type {
  ApiErrorResponse,
  BuildMergeMode,
  BuildResponse,
  CreateJobResponse,
  JobResponse,
  OutputDocumentResponse,
  PageResponse,
  PageSummaryResponse,
  PublicConfigResponse,
  RetryPageAcceptedResponse,
  SinglePagePreviewResponse,
  TestConnectionResponse,
  UpdateConfigRequest,
} from "../types/index.ts";

export type Fetcher = (input: string, init?: RequestInit) => Promise<Response>;

export interface ApiClientOptions {
  baseUrl?: string;
  fetcher?: Fetcher;
}

export interface CreateJobInput {
  filename: string;
  pdf: Blob;
}

export class ApiClientError extends Error {
  readonly status: number;

  readonly code: string | null;

  readonly details: Record<string, unknown> | null;

  constructor(args: { status: number; code: string | null; message: string; details: Record<string, unknown> | null }) {
    super(args.message);
    this.name = "ApiClientError";
    this.status = args.status;
    this.code = args.code;
    this.details = args.details;
  }
}

interface ClientRuntimeOptions {
  baseUrl: string;
  fetcher: Fetcher;
}

interface JsonRequestArgs {
  method?: "GET" | "POST" | "PUT";
  jsonBody?: unknown;
  body?: BodyInit | null;
  headers?: HeadersInit;
  signal?: AbortSignal;
}

function resolveClientOptions(options: ApiClientOptions): ClientRuntimeOptions {
  const baseUrl = normalizeBaseUrl(options.baseUrl ?? "");
  const fetcher: Fetcher = options.fetcher ?? defaultFetcher;
  return { baseUrl, fetcher };
}

function normalizeBaseUrl(baseUrl: string): string {
  const trimmed = baseUrl.trim();
  if (!trimmed) {
    return "";
  }
  return trimmed.endsWith("/") ? trimmed.slice(0, -1) : trimmed;
}

function defaultFetcher(input: string, init?: RequestInit): Promise<Response> {
  return fetch(input, init);
}

function joinUrl(baseUrl: string, path: string): string {
  if (!path.startsWith("/")) {
    throw new Error(`path must start with '/': ${path}`);
  }
  return `${baseUrl}${path}`;
}

function toApiErrorResponse(value: unknown): ApiErrorResponse | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const detail = (value as { detail?: unknown }).detail;
  if (!detail || typeof detail !== "object") {
    return null;
  }
  const code = (detail as { code?: unknown }).code;
  const message = (detail as { message?: unknown }).message;
  const details = (detail as { details?: unknown }).details;
  if (typeof code !== "string" || typeof message !== "string") {
    return null;
  }
  if (details !== undefined && details !== null && typeof details !== "object") {
    return null;
  }
  return {
    detail: {
      code,
      message,
      details: (details as Record<string, unknown> | null | undefined) ?? null,
    },
  };
}

async function parseResponsePayload(response: Response): Promise<unknown> {
  const bodyText = await response.text();
  if (!bodyText.trim()) {
    return null;
  }
  try {
    return JSON.parse(bodyText) as unknown;
  } catch {
    return bodyText;
  }
}

async function requestJson<T>(client: ClientRuntimeOptions, url: string, args: JsonRequestArgs = {}): Promise<T> {
  const headers = new Headers(args.headers);
  const init: RequestInit = {
    method: args.method ?? "GET",
    headers,
  };
  if (!headers.has("Accept")) {
    headers.set("Accept", "application/json");
  }

  if (args.jsonBody !== undefined) {
    init.body = JSON.stringify(args.jsonBody);
    if (!headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
  } else if (args.body !== undefined) {
    init.body = args.body;
  }

  if (args.signal !== undefined) {
    init.signal = args.signal;
  }

  const response = await client.fetcher(url, init);
  const payload = await parseResponsePayload(response);
  if (!response.ok) {
    const errorPayload = toApiErrorResponse(payload);
    throw new ApiClientError({
      status: response.status,
      code: errorPayload?.detail.code ?? null,
      message: errorPayload?.detail.message ?? `HTTP ${response.status}`,
      details: errorPayload?.detail.details ?? null,
    });
  }
  return payload as T;
}

function normalizeJobPath(jobId: string): string {
  const normalized = jobId.trim();
  if (!normalized) {
    throw new Error("job_id cannot be empty");
  }
  return encodeURIComponent(normalized);
}

function normalizePageNum(pageNum: number): number {
  if (!Number.isInteger(pageNum) || pageNum <= 0) {
    throw new Error(`page_num must be a positive integer, got ${pageNum}`);
  }
  return pageNum;
}

export class JobApiClient {
  private readonly client: ClientRuntimeOptions;

  constructor(options: ApiClientOptions = {}) {
    this.client = resolveClientOptions(options);
  }

  async createJob(input: CreateJobInput): Promise<CreateJobResponse> {
    const formData = new FormData();
    formData.append("file", input.pdf, input.filename);
    return requestJson<CreateJobResponse>(
      this.client,
      joinUrl(this.client.baseUrl, "/api/jobs"),
      {
        method: "POST",
        body: formData,
      },
    );
  }

  async getJob(jobId: string): Promise<JobResponse> {
    return requestJson<JobResponse>(
      this.client,
      joinUrl(this.client.baseUrl, `/api/jobs/${normalizeJobPath(jobId)}`),
    );
  }

  async listPages(jobId: string): Promise<PageSummaryResponse[]> {
    return requestJson<PageSummaryResponse[]>(
      this.client,
      joinUrl(this.client.baseUrl, `/api/jobs/${normalizeJobPath(jobId)}/pages`),
    );
  }

  async getPage(jobId: string, pageNum: number): Promise<PageResponse> {
    return requestJson<PageResponse>(
      this.client,
      joinUrl(
        this.client.baseUrl,
        `/api/jobs/${normalizeJobPath(jobId)}/pages/${normalizePageNum(pageNum)}`,
      ),
    );
  }

  async savePage(jobId: string, pageNum: number, content: string): Promise<PageResponse> {
    return requestJson<PageResponse>(
      this.client,
      joinUrl(
        this.client.baseUrl,
        `/api/jobs/${normalizeJobPath(jobId)}/pages/${normalizePageNum(pageNum)}`,
      ),
      {
        method: "PUT",
        jsonBody: { content },
      },
    );
  }

  async retryPage(jobId: string, pageNum: number): Promise<RetryPageAcceptedResponse> {
    return requestJson<RetryPageAcceptedResponse>(
      this.client,
      joinUrl(
        this.client.baseUrl,
        `/api/jobs/${normalizeJobPath(jobId)}/pages/${normalizePageNum(pageNum)}/retry`,
      ),
      {
        method: "POST",
      },
    );
  }

  async buildOutput(jobId: string, mergeMode: BuildMergeMode = "direct"): Promise<BuildResponse> {
    return requestJson<BuildResponse>(
      this.client,
      joinUrl(this.client.baseUrl, `/api/jobs/${normalizeJobPath(jobId)}/build`),
      {
        method: "POST",
        jsonBody: { merge_mode: mergeMode },
      },
    );
  }

  async getOutput(jobId: string): Promise<OutputDocumentResponse> {
    return requestJson<OutputDocumentResponse>(
      this.client,
      joinUrl(this.client.baseUrl, `/api/jobs/${normalizeJobPath(jobId)}/output`),
    );
  }

  async saveOutput(jobId: string, content: string): Promise<OutputDocumentResponse> {
    return requestJson<OutputDocumentResponse>(
      this.client,
      joinUrl(this.client.baseUrl, `/api/jobs/${normalizeJobPath(jobId)}/output`),
      {
        method: "PUT",
        jsonBody: { content },
      },
    );
  }

  async discardOutput(jobId: string): Promise<JobResponse> {
    return requestJson<JobResponse>(
      this.client,
      joinUrl(this.client.baseUrl, `/api/jobs/${normalizeJobPath(jobId)}/output/discard`),
      {
        method: "POST",
      },
    );
  }

  getSourceDocumentUrl(jobId: string): string {
    return joinUrl(this.client.baseUrl, `/api/jobs/${normalizeJobPath(jobId)}/source`);
  }

  getOutputDownloadUrl(jobId: string): string {
    return joinUrl(this.client.baseUrl, `/api/jobs/${normalizeJobPath(jobId)}/output/download`);
  }
}

export class ConfigApiClient {
  private readonly client: ClientRuntimeOptions;

  constructor(options: ApiClientOptions = {}) {
    this.client = resolveClientOptions(options);
  }

  async getPublicConfig(): Promise<PublicConfigResponse> {
    return requestJson<PublicConfigResponse>(
      this.client,
      joinUrl(this.client.baseUrl, "/api/config"),
    );
  }

  async updateConfig(request: UpdateConfigRequest): Promise<PublicConfigResponse> {
    return requestJson<PublicConfigResponse>(
      this.client,
      joinUrl(this.client.baseUrl, "/api/config"),
      {
        method: "PUT",
        jsonBody: request,
      },
    );
  }

  async restoreInitialConfig(): Promise<PublicConfigResponse> {
    return requestJson<PublicConfigResponse>(
      this.client,
      joinUrl(this.client.baseUrl, "/api/config/reset"),
      {
        method: "POST",
      },
    );
  }

  async testConnection(): Promise<TestConnectionResponse> {
    return requestJson<TestConnectionResponse>(
      this.client,
      joinUrl(this.client.baseUrl, "/api/config/test-connection"),
      {
        method: "POST",
      },
    );
  }
}

export interface SinglePagePreviewOptions extends ApiClientOptions {
  signal?: AbortSignal;
}

export async function postSinglePagePreview(
  file: File,
  pageNum: number,
  options: SinglePagePreviewOptions = {},
): Promise<SinglePagePreviewResponse> {
  const client = resolveClientOptions(options);
  const normalizedPageNum = normalizePageNum(pageNum);
  const formData = new FormData();
  formData.append("file", file, file.name);
  formData.append("page_num", String(normalizedPageNum));
  return requestJson<SinglePagePreviewResponse>(
    client,
    joinUrl(client.baseUrl, "/api/extraction/single-page-preview"),
    {
      method: "POST",
      body: formData,
      signal: options.signal,
    },
  );
}
