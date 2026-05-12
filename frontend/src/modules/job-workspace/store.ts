export type WorkspaceMode = "page" | "output";

export type SinglePageTestStatus = "idle" | "running" | "done" | "failed";

export interface SinglePageTestError {
  code: string;
  message: string;
}

export interface SinglePageTestState {
  status: SinglePageTestStatus;
  pageNum: number | null;
  content: string | null;
  error: SinglePageTestError | null;
}

export interface WorkspaceStoreState {
  job_id: string | null;
  current_page_num: number;
  workspace_mode: WorkspaceMode;
  is_building_busy: boolean;
  singlePageTest: SinglePageTestState;
}

export interface WorkspaceStore {
  getState(): WorkspaceStoreState;
  setState(patch: Partial<WorkspaceStoreState>): WorkspaceStoreState;
  subscribe(listener: (state: WorkspaceStoreState) => void): () => void;
}

export const IDLE_SINGLE_PAGE_TEST: SinglePageTestState = {
  status: "idle",
  pageNum: null,
  content: null,
  error: null,
};

const DEFAULT_STATE: WorkspaceStoreState = {
  job_id: null,
  current_page_num: 1,
  workspace_mode: "page",
  is_building_busy: false,
  singlePageTest: cloneSinglePageTest(IDLE_SINGLE_PAGE_TEST),
};

function normalizeWorkspaceMode(
  value: WorkspaceStoreState["workspace_mode"] | undefined,
  fallback: WorkspaceMode,
): WorkspaceMode {
  if (value === "page" || value === "output") {
    return value;
  }
  return fallback;
}

function normalizePageNum(value: number | undefined, fallback: number): number {
  if (typeof value === "number" && Number.isInteger(value) && value > 0) {
    return value;
  }
  return fallback;
}

function normalizeJobId(value: string | null | undefined, fallback: string | null): string | null {
  if (value === null) {
    return null;
  }
  if (typeof value === "string") {
    const normalized = value.trim();
    return normalized || null;
  }
  return fallback;
}

function normalizeBusy(value: boolean | undefined, fallback: boolean): boolean {
  if (typeof value === "boolean") {
    return value;
  }
  return fallback;
}

function isValidSinglePageTestStatus(value: unknown): value is SinglePageTestStatus {
  return value === "idle" || value === "running" || value === "done" || value === "failed";
}

function normalizeOptionalPageNum(value: unknown): number | null {
  if (typeof value === "number" && Number.isInteger(value) && value > 0) {
    return value;
  }
  return null;
}

function normalizeOptionalContent(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function normalizeSinglePageTestError(value: unknown): SinglePageTestError | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const code = (value as { code?: unknown }).code;
  const message = (value as { message?: unknown }).message;
  if (typeof code !== "string" || typeof message !== "string") {
    return null;
  }
  return { code, message };
}

function cloneSinglePageTest(value: SinglePageTestState): SinglePageTestState {
  return {
    status: value.status,
    pageNum: value.pageNum,
    content: value.content,
    error: value.error ? { code: value.error.code, message: value.error.message } : null,
  };
}

function normalizeSinglePageTest(
  value: SinglePageTestState | undefined,
  fallback: SinglePageTestState,
): SinglePageTestState {
  if (!value || typeof value !== "object") {
    return cloneSinglePageTest(fallback);
  }
  const status = isValidSinglePageTestStatus(value.status) ? value.status : fallback.status;
  return {
    status,
    pageNum: normalizeOptionalPageNum(value.pageNum),
    content: normalizeOptionalContent(value.content),
    error: normalizeSinglePageTestError(value.error),
  };
}

function makeNextState(
  fallback: WorkspaceStoreState,
  patch: Partial<WorkspaceStoreState>,
): WorkspaceStoreState {
  return {
    job_id: normalizeJobId(patch.job_id, fallback.job_id),
    current_page_num: normalizePageNum(patch.current_page_num, fallback.current_page_num),
    workspace_mode: normalizeWorkspaceMode(patch.workspace_mode, fallback.workspace_mode),
    is_building_busy: normalizeBusy(patch.is_building_busy, fallback.is_building_busy),
    singlePageTest: Object.prototype.hasOwnProperty.call(patch, "singlePageTest")
      ? normalizeSinglePageTest(patch.singlePageTest, fallback.singlePageTest)
      : cloneSinglePageTest(fallback.singlePageTest),
  };
}

function singlePageTestEqual(left: SinglePageTestState, right: SinglePageTestState): boolean {
  if (
    left.status !== right.status
    || left.pageNum !== right.pageNum
    || left.content !== right.content
  ) {
    return false;
  }
  if (left.error === right.error) {
    return true;
  }
  if (!left.error || !right.error) {
    return false;
  }
  return left.error.code === right.error.code && left.error.message === right.error.message;
}

function statesEqual(left: WorkspaceStoreState, right: WorkspaceStoreState): boolean {
  return (
    left.job_id === right.job_id
    && left.current_page_num === right.current_page_num
    && left.workspace_mode === right.workspace_mode
    && left.is_building_busy === right.is_building_busy
    && singlePageTestEqual(left.singlePageTest, right.singlePageTest)
  );
}

function snapshotState(state: WorkspaceStoreState): WorkspaceStoreState {
  return {
    ...state,
    singlePageTest: cloneSinglePageTest(state.singlePageTest),
  };
}

export function createWorkspaceStore(
  initialState: Partial<WorkspaceStoreState> = {},
): WorkspaceStore {
  let state = makeNextState(DEFAULT_STATE, initialState);
  const listeners = new Set<(nextState: WorkspaceStoreState) => void>();

  return {
    getState(): WorkspaceStoreState {
      return snapshotState(state);
    },
    setState(patch: Partial<WorkspaceStoreState>): WorkspaceStoreState {
      const nextState = makeNextState(state, patch);
      if (statesEqual(state, nextState)) {
        return snapshotState(state);
      }
      state = nextState;
      const snapshot = snapshotState(state);
      for (const listener of listeners) {
        listener(snapshotState(state));
      }
      return snapshot;
    },
    subscribe(listener: (nextState: WorkspaceStoreState) => void): () => void {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}
