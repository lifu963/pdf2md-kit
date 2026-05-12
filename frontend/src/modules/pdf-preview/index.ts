import type { JobApiClient } from "../../shared/api/index.ts";
import type { WorkspaceStore } from "../job-workspace/store.ts";

type PdfPaneJobApiClient = Pick<JobApiClient, "getSourceDocumentUrl">;
type PdfPaneStore = Pick<WorkspaceStore, "getState">;

export interface PdfPaneViewModel {
  document_url: string;
  current_page_num: number;
}

export interface PdfPaneControllerDeps {
  jobApiClient: PdfPaneJobApiClient;
  workspaceStore: PdfPaneStore;
  onChangePage: (page_num: number) => Promise<unknown> | unknown;
}

export interface PdfPaneController {
  getViewModel(): PdfPaneViewModel;
  setCurrentPage(page_num: number): Promise<void>;
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

export function createPdfPaneController(
  job_id: string,
  deps: PdfPaneControllerDeps,
): PdfPaneController {
  const normalizedJobId = normalizeJobId(job_id);
  const documentUrl = deps.jobApiClient.getSourceDocumentUrl(normalizedJobId);

  return {
    getViewModel(): PdfPaneViewModel {
      return {
        document_url: documentUrl,
        current_page_num: deps.workspaceStore.getState().current_page_num,
      };
    },

    async setCurrentPage(page_num: number): Promise<void> {
      await deps.onChangePage(normalizePageNum(page_num));
    },
  };
}
