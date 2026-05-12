import type { JobStreamEvent } from "../types/index.ts";

type EventListenerLike = (event: { data?: string } | unknown) => void;

interface EventSourceLike {
  addEventListener(type: string, listener: EventListenerLike): void;
  removeEventListener(type: string, listener: EventListenerLike): void;
  close(): void;
}

type EventSourceFactory = (url: string) => EventSourceLike;

export interface JobStreamSubscription {
  close(): void;
}

export interface JobStreamSubscriber {
  onOpen?: () => void;
  onEvent: (payload: JobStreamEvent) => void;
  onError?: (error: unknown) => void;
}

export interface JobStreamClientOptions {
  baseUrl?: string;
  eventSourceFactory?: EventSourceFactory;
}

function normalizeBaseUrl(baseUrl: string): string {
  const trimmed = baseUrl.trim();
  if (!trimmed) {
    return "";
  }
  return trimmed.endsWith("/") ? trimmed.slice(0, -1) : trimmed;
}

function buildUrl(baseUrl: string, path: string): string {
  if (!path.startsWith("/")) {
    throw new Error(`path must start with '/': ${path}`);
  }
  return `${baseUrl}${path}`;
}

function normalizeJobId(jobId: string): string {
  const normalized = jobId.trim();
  if (!normalized) {
    throw new Error("job_id cannot be empty");
  }
  return encodeURIComponent(normalized);
}

function defaultEventSourceFactory(url: string): EventSourceLike {
  const EventSourceCtor = (globalThis as { EventSource?: new (input: string) => EventSourceLike }).EventSource;
  if (!EventSourceCtor) {
    throw new Error("EventSource is not available in current runtime");
  }
  return new EventSourceCtor(url);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object";
}

function isValidStreamEvent(value: unknown): value is JobStreamEvent {
  if (!isRecord(value) || typeof value.type !== "string") {
    return false;
  }

  if (value.type === "page") {
    return (
      typeof value.page_num === "number"
      && typeof value.status === "string"
      && typeof value.processed_count === "number"
      && typeof value.total_pages === "number"
    );
  }

  if (value.type === "complete") {
    return (
      typeof value.processed_count === "number"
      && typeof value.total_pages === "number"
      && Array.isArray(value.succeeded_pages)
      && Array.isArray(value.failed_pages)
    );
  }

  if (value.type === "failed") {
    return typeof value.detail === "string";
  }

  return false;
}

function parseStreamPayload(rawData: string): JobStreamEvent {
  const parsed = JSON.parse(rawData) as unknown;
  if (!isValidStreamEvent(parsed)) {
    throw new Error(`unsupported stream payload: ${rawData}`);
  }
  return parsed;
}

export class JobStreamClient {
  private readonly baseUrl: string;

  private readonly eventSourceFactory: EventSourceFactory;

  constructor(options: JobStreamClientOptions = {}) {
    this.baseUrl = normalizeBaseUrl(options.baseUrl ?? "");
    this.eventSourceFactory = options.eventSourceFactory ?? defaultEventSourceFactory;
  }

  subscribeJobEvents(jobId: string, subscriber: JobStreamSubscriber): JobStreamSubscription {
    const source = this.eventSourceFactory(
      buildUrl(this.baseUrl, `/api/jobs/${normalizeJobId(jobId)}/stream`),
    );

    let closed = false;

    const handleOpen: EventListenerLike = () => {
      subscriber.onOpen?.();
    };

    const handleMessage: EventListenerLike = (event) => {
      try {
        const payloadText = String((event as { data?: unknown }).data ?? "");
        const payload = parseStreamPayload(payloadText);
        subscriber.onEvent(payload);
      } catch (error) {
        subscriber.onError?.(error);
      }
    };

    const handleError: EventListenerLike = (error) => {
      subscriber.onError?.(error);
    };

    source.addEventListener("open", handleOpen);
    source.addEventListener("message", handleMessage);
    source.addEventListener("error", handleError);

    return {
      close(): void {
        if (closed) {
          return;
        }
        closed = true;
        source.removeEventListener("open", handleOpen);
        source.removeEventListener("message", handleMessage);
        source.removeEventListener("error", handleError);
        source.close();
      },
    };
  }
}
