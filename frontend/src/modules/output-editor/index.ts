import type { JobApiClient } from "../../shared/api/index.ts";
import type { JobResponse, JobStatus, OutputDocumentResponse } from "../../shared/types/index.ts";
import type { WorkspaceStore } from "../job-workspace/store.ts";

type OutputPaneJobApiClient = Pick<JobApiClient, "saveOutput" | "getOutputDownloadUrl">;
type OutputPaneStore = Pick<WorkspaceStore, "getState">;

export interface OutputPaneViewModel {
  job_id: string;
  state: "ready" | "unavailable";
  title: string;
  message: string;
  content: string;
  updated_at: string | null;
  can_edit: boolean;
  can_save: boolean;
  download_url: string | null;
  is_building_busy: boolean;
}

export interface OutputPaneControllerDeps {
  jobApiClient: OutputPaneJobApiClient;
  workspaceStore: OutputPaneStore;
}

export interface OutputPaneController {
  getViewModel(job: JobResponse | null, output: OutputDocumentResponse | null): OutputPaneViewModel;
  save(job: JobResponse | null, content: string): Promise<OutputDocumentResponse>;
}

function normalizeJobId(job_id: string): string {
  const normalized = job_id.trim();
  if (!normalized) {
    throw new Error("job_id cannot be empty");
  }
  return normalized;
}

function cloneOutputDocument(output: OutputDocumentResponse | null): OutputDocumentResponse | null {
  if (!output) {
    return null;
  }
  return { ...output };
}

function resolveUnavailableMessage(status: JobStatus | null): string {
  if (status === "building") {
    return "构建中，暂时无法编辑 output。";
  }
  if (status === "extracting") {
    return "提取尚未完成，暂时无法编辑 output。";
  }
  if (status === "extracted") {
    return "请先构建 output 后再编辑。";
  }
  if (status === "failed") {
    return "任务失败，当前 output 不可编辑。";
  }
  return "当前 output 不可编辑。";
}

export function createOutputPaneController(
  job_id: string,
  deps: OutputPaneControllerDeps,
): OutputPaneController {
  const normalizedJobId = normalizeJobId(job_id);

  function buildViewModel(
    job: JobResponse | null,
    output: OutputDocumentResponse | null,
  ): OutputPaneViewModel {
    const isBuildingBusy = deps.workspaceStore.getState().is_building_busy;
    const isReady = job?.status === "ready";
    const canEdit = Boolean(isReady && !isBuildingBusy);

    return {
      job_id: normalizedJobId,
      state: isReady ? "ready" : "unavailable",
      title: isReady ? "输出文档" : "Output 不可编辑",
      message: isReady
        ? (isBuildingBusy ? "构建中，暂时无法保存 output。" : "当前 output 已就绪，可直接编辑。")
        : resolveUnavailableMessage(job?.status ?? null),
      content: isReady ? (output?.content ?? "") : "",
      updated_at: isReady ? (output?.updated_at ?? null) : null,
      can_edit: canEdit,
      can_save: canEdit,
      download_url: isReady ? deps.jobApiClient.getOutputDownloadUrl(normalizedJobId) : null,
      is_building_busy: isBuildingBusy,
    };
  }

  return {
    getViewModel(job: JobResponse | null, output: OutputDocumentResponse | null): OutputPaneViewModel {
      return buildViewModel(job, cloneOutputDocument(output));
    },

    async save(job: JobResponse | null, content: string): Promise<OutputDocumentResponse> {
      const viewModel = buildViewModel(job, null);
      if (!viewModel.can_save) {
        throw new Error("output editor is unavailable in current status");
      }
      return deps.jobApiClient.saveOutput(normalizedJobId, content);
    },
  };
}
