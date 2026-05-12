import { ApiClientError } from "../../shared/api/index.ts";
import type { JobStreamClient, JobStreamSubscription } from "../../shared/stream/index.ts";
import type {
  JobResponse,
  JobStatus,
  JobStreamEvent,
  OutputDocumentResponse,
  PageResponse,
  PageSummaryResponse,
  SinglePagePreviewResponse,
} from "../../shared/types/index.ts";
import type { WorkspaceQueryHooks } from "./query-hooks.ts";
import type { SinglePageTestError, WorkspaceStore } from "./store.ts";

type JobWorkspaceQueryHooks = Pick<
  WorkspaceQueryHooks,
  "useJobQuery" | "usePagesQuery" | "usePageQuery" | "useOutputQuery"
>;
type JobWorkspaceStreamClient = Pick<JobStreamClient, "subscribeJobEvents">;

export interface BuildingRecoveryOptions {
  waitForNextPoll?: () => Promise<void>;
  maxPollAttempts?: number;
}

export type SinglePagePreviewFn = (
  file: File,
  pageNum: number,
) => Promise<SinglePagePreviewResponse>;

export interface RunSinglePageTestInput {
  file: File | null;
  previewReady: boolean;
  pageNum: number;
}

export interface JobWorkspaceControllerDeps {
  store: WorkspaceStore;
  queryHooks: JobWorkspaceQueryHooks;
  streamClient: JobWorkspaceStreamClient;
  buildingRecovery?: BuildingRecoveryOptions;
  singlePagePreviewFn?: SinglePagePreviewFn;
}

export interface JobWorkspaceSnapshot {
  job: JobResponse | null;
  pages: PageSummaryResponse[];
  current_page: PageResponse | null;
  output_document: OutputDocumentResponse | null;
  is_stream_connected: boolean;
}

export interface JobWorkspaceController {
  bootstrap(job_id: string): Promise<JobWorkspaceSnapshot>;
  refresh(): Promise<JobWorkspaceSnapshot>;
  switchPage(page_num: number): Promise<JobWorkspaceSnapshot>;
  getSnapshot(): JobWorkspaceSnapshot;
  runSinglePageTest(input: RunSinglePageTestInput): Promise<void>;
  resetSinglePageTest(): void;
  dispose(): void;
}

const PAGE_WORKSPACE_STATUSES: JobStatus[] = ["extracting", "extracted"];

function isPageWorkspaceStatus(status: JobStatus): boolean {
  return PAGE_WORKSPACE_STATUSES.includes(status);
}

function normalizeJobId(job_id: string): string {
  const normalized = job_id.trim();
  if (!normalized) {
    throw new Error("job_id cannot be empty");
  }
  return normalized;
}

function normalizePageNum(page_num: number): number {
  if (!Number.isInteger(page_num) || page_num <= 0) {
    throw new Error(`page_num must be a positive integer, got ${page_num}`);
  }
  return page_num;
}

function cloneJob(job: JobResponse): JobResponse {
  return {
    ...job,
    succeeded_pages: [...job.succeeded_pages],
    failed_pages: [...job.failed_pages],
  };
}

function clonePageSummary(summary: PageSummaryResponse): PageSummaryResponse {
  return { ...summary };
}

function clonePage(page: PageResponse): PageResponse {
  return { ...page };
}

function cloneOutput(output: OutputDocumentResponse): OutputDocumentResponse {
  return { ...output };
}

function resolveCurrentPageNum(totalPages: number, requestedPageNum: number): number {
  if (totalPages <= 0) {
    return 1;
  }
  if (requestedPageNum > totalPages) {
    return totalPages;
  }
  return requestedPageNum;
}

function replacePageSummary(
  summaries: PageSummaryResponse[],
  nextSummary: PageSummaryResponse,
): PageSummaryResponse[] {
  const nextSummaries = summaries.map(clonePageSummary);
  const existingIndex = nextSummaries.findIndex((item) => item.page_num === nextSummary.page_num);
  if (existingIndex >= 0) {
    nextSummaries[existingIndex] = clonePageSummary(nextSummary);
  } else {
    nextSummaries.push(clonePageSummary(nextSummary));
    nextSummaries.sort((left, right) => left.page_num - right.page_num);
  }
  return nextSummaries;
}

function collectProcessedPages(summaries: PageSummaryResponse[]): {
  succeeded_pages: number[];
  failed_pages: number[];
} {
  const succeeded_pages: number[] = [];
  const failed_pages: number[] = [];

  for (const summary of summaries) {
    if (summary.status === "done") {
      succeeded_pages.push(summary.page_num);
    } else if (summary.status === "failed") {
      failed_pages.push(summary.page_num);
    }
  }

  return { succeeded_pages, failed_pages };
}

function normalizePollAttempts(value: number | undefined): number {
  if (!value) {
    return 60;
  }
  if (!Number.isInteger(value) || value <= 0) {
    return 60;
  }
  return value;
}

function defaultWaitForNextPoll(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 300);
  });
}

function toSinglePageTestError(error: unknown): SinglePageTestError {
  if (error instanceof ApiClientError) {
    return {
      code: error.code ?? "UNEXPECTED_ERROR",
      message: error.message || "请求失败",
    };
  }
  if (error instanceof Error) {
    return { code: "UNEXPECTED_ERROR", message: error.message || "请求失败" };
  }
  return { code: "UNEXPECTED_ERROR", message: String(error) };
}

export function createJobWorkspaceController(
  deps: JobWorkspaceControllerDeps,
): JobWorkspaceController {
  let activeJobId: string | null = null;
  let activeJob: JobResponse | null = null;
  let pageSummaries: PageSummaryResponse[] = [];
  let currentPage: PageResponse | null = null;
  let outputDocument: OutputDocumentResponse | null = null;
  let streamSubscription: JobStreamSubscription | null = null;
  let streamEventQueue: Promise<void> = Promise.resolve();

  function closeStream(): void {
    if (!streamSubscription) {
      return;
    }
    streamSubscription.close();
    streamSubscription = null;
  }

  function getStatePageNum(): number {
    const pageNum = deps.store.getState().current_page_num;
    return normalizePageNum(pageNum);
  }

  async function queryJob(jobId: string): Promise<JobResponse> {
    const payload = await deps.queryHooks.useJobQuery(jobId).queryFn();
    return payload;
  }

  async function queryPages(jobId: string): Promise<PageSummaryResponse[]> {
    const payload = await deps.queryHooks.usePagesQuery(jobId).queryFn();
    return payload;
  }

  async function queryPage(jobId: string, pageNum: number): Promise<PageResponse> {
    const payload = await deps.queryHooks.usePageQuery(jobId, pageNum).queryFn();
    return payload;
  }

  async function queryOutput(jobId: string): Promise<OutputDocumentResponse> {
    const payload = await deps.queryHooks.useOutputQuery(jobId).queryFn();
    return payload;
  }

  async function recoverFromBuilding(jobId: string, initialJob: JobResponse): Promise<JobResponse> {
    const waitForNextPoll = deps.buildingRecovery?.waitForNextPoll ?? defaultWaitForNextPoll;
    const maxPollAttempts = normalizePollAttempts(deps.buildingRecovery?.maxPollAttempts);
    let attempts = 0;
    let nextJob = initialJob;

    while (nextJob.status === "building" && attempts < maxPollAttempts) {
      attempts += 1;
      await waitForNextPoll();
      nextJob = await queryJob(jobId);
    }
    return nextJob;
  }

  function buildSnapshot(): JobWorkspaceSnapshot {
    return {
      job: activeJob ? cloneJob(activeJob) : null,
      pages: pageSummaries.map(clonePageSummary),
      current_page: currentPage ? clonePage(currentPage) : null,
      output_document: outputDocument ? cloneOutput(outputDocument) : null,
      is_stream_connected: streamSubscription !== null,
    };
  }

  function ensurePageWorkspaceAvailable(): void {
    if (!activeJobId || !activeJob || !isPageWorkspaceStatus(activeJob.status)) {
      throw new Error("page workspace is unavailable in current status");
    }
  }

  async function reloadCurrentPageIfMatched(pageNum: number): Promise<void> {
    if (!activeJobId) {
      return;
    }
    if (getStatePageNum() !== pageNum) {
      return;
    }
    currentPage = await queryPage(activeJobId, pageNum);
  }

  async function processStreamEvent(event: JobStreamEvent): Promise<void> {
    if (!activeJob) {
      return;
    }

    if (event.type === "page") {
      pageSummaries = replacePageSummary(pageSummaries, {
        page_num: event.page_num,
        status: event.status,
      });
      const processed = collectProcessedPages(pageSummaries);
      activeJob = {
        ...activeJob,
        total_pages: event.total_pages,
        processed_count: event.processed_count,
        succeeded_pages: processed.succeeded_pages,
        failed_pages: processed.failed_pages,
      };
      await reloadCurrentPageIfMatched(event.page_num);
      return;
    }

    if (event.type === "complete") {
      activeJob = {
        ...activeJob,
        status: "extracted",
        total_pages: event.total_pages,
        processed_count: event.processed_count,
        succeeded_pages: [...event.succeeded_pages],
        failed_pages: [...event.failed_pages],
      };
      deps.store.setState({
        workspace_mode: "page",
        is_building_busy: false,
      });
      closeStream();
      return;
    }

    activeJob = {
      ...activeJob,
      status: "failed",
    };
    deps.store.setState({ is_building_busy: false });
    closeStream();
  }

  function enqueueStreamEvent(event: JobStreamEvent): Promise<void> {
    streamEventQueue = streamEventQueue
      .catch(() => undefined)
      .then(async () => {
        await processStreamEvent(event);
      });
    return streamEventQueue;
  }

  return {
    async bootstrap(job_id: string): Promise<JobWorkspaceSnapshot> {
      const normalizedJobId = normalizeJobId(job_id);

      closeStream();
      activeJobId = normalizedJobId;
      activeJob = null;
      pageSummaries = [];
      currentPage = null;
      outputDocument = null;
      deps.store.setState({ job_id: normalizedJobId });

      activeJob = await queryJob(normalizedJobId);
      if (activeJob.status === "building") {
        deps.store.setState({
          workspace_mode: "page",
          is_building_busy: true,
        });
        activeJob = await recoverFromBuilding(normalizedJobId, activeJob);
      }

      if (activeJob.status === "building") {
        return buildSnapshot();
      }
      deps.store.setState({ is_building_busy: false });

      if (activeJob.status === "ready") {
        deps.store.setState({ workspace_mode: "output" });
        outputDocument = await queryOutput(normalizedJobId);
        return buildSnapshot();
      }

      if (isPageWorkspaceStatus(activeJob.status)) {
        deps.store.setState({ workspace_mode: "page" });
        pageSummaries = await queryPages(normalizedJobId);

        const requestedPageNum = getStatePageNum();
        const resolvedPageNum = resolveCurrentPageNum(activeJob.total_pages, requestedPageNum);
        if (resolvedPageNum !== requestedPageNum) {
          deps.store.setState({ current_page_num: resolvedPageNum });
        }
        currentPage = await queryPage(normalizedJobId, resolvedPageNum);

        if (activeJob.status === "extracting") {
          streamSubscription = deps.streamClient.subscribeJobEvents(normalizedJobId, {
            onEvent: (event) => enqueueStreamEvent(event),
            onError: () => {},
          });
        }
      }

      return buildSnapshot();
    },

    async refresh(): Promise<JobWorkspaceSnapshot> {
      if (!activeJobId) {
        throw new Error("workspace is not bootstrapped");
      }
      return this.bootstrap(activeJobId);
    },

    async switchPage(page_num: number): Promise<JobWorkspaceSnapshot> {
      ensurePageWorkspaceAvailable();
      const pageNum = normalizePageNum(page_num);
      if (activeJob && pageNum > activeJob.total_pages) {
        throw new Error(`page_num ${pageNum} exceeds total_pages ${activeJob.total_pages}`);
      }
      deps.store.setState({ current_page_num: pageNum });
      currentPage = await queryPage(activeJobId as string, pageNum);
      return buildSnapshot();
    },

    getSnapshot(): JobWorkspaceSnapshot {
      return buildSnapshot();
    },

    async runSinglePageTest(input: RunSinglePageTestInput): Promise<void> {
      if (!input || !input.file) {
        throw new Error("preparedFile is required to run a single-page test");
      }
      if (!input.previewReady) {
        throw new Error("pdf preview is not ready yet");
      }
      const storeState = deps.store.getState();
      if (storeState.job_id) {
        throw new Error("single-page test is not allowed when a job is active");
      }
      if (storeState.singlePageTest.status === "running") {
        throw new Error("single-page test is already running");
      }
      const previewFn = deps.singlePagePreviewFn;
      if (!previewFn) {
        throw new Error("singlePagePreviewFn dependency is not configured");
      }
      const snapshotPageNum = normalizePageNum(input.pageNum);

      deps.store.setState({
        singlePageTest: {
          status: "running",
          pageNum: snapshotPageNum,
          content: null,
          error: null,
        },
      });

      try {
        const response = await previewFn(input.file, snapshotPageNum);
        deps.store.setState({
          singlePageTest: {
            status: "done",
            pageNum: snapshotPageNum,
            content: response.content,
            error: null,
          },
        });
      } catch (error) {
        deps.store.setState({
          singlePageTest: {
            status: "failed",
            pageNum: snapshotPageNum,
            content: null,
            error: toSinglePageTestError(error),
          },
        });
      }
    },

    resetSinglePageTest(): void {
      deps.store.setState({
        singlePageTest: {
          status: "idle",
          pageNum: null,
          content: null,
          error: null,
        },
      });
    },

    dispose(): void {
      closeStream();
    },
  };
}

