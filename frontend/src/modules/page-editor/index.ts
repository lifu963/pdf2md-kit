import type { PageResponse } from "../../shared/types/index.ts";
import type { SinglePageTestState, WorkspaceStore } from "../job-workspace/store.ts";

type PagePaneStore = Pick<WorkspaceStore, "getState">;

export type PagePaneState =
  | "pending"
  | "extracting"
  | "done"
  | "failed"
  | "test-running"
  | "test-done"
  | "test-failed";

export interface PagePaneViewModel {
  current_page_num: number;
  state: PagePaneState;
  title: string;
  message: string;
  content: string | null;
  error: string | null;
}

export interface PagePaneController {
  getViewModel(page: PageResponse | null): PagePaneViewModel;
}

export interface PagePaneControllerDeps {
  workspaceStore: PagePaneStore;
}

const IDLE_SINGLE_PAGE_TEST: SinglePageTestState = {
  status: "idle",
  pageNum: null,
  content: null,
  error: null,
};

function normalizeCurrentPageNum(value: number): number {
  if (Number.isInteger(value) && value > 0) {
    return value;
  }
  return 1;
}

function resolveTestPageNum(
  snapshotPageNum: number | null,
  fallbackPageNum: number,
): number {
  if (snapshotPageNum !== null && Number.isInteger(snapshotPageNum) && snapshotPageNum > 0) {
    return snapshotPageNum;
  }
  return fallbackPageNum;
}

export function buildPagePaneViewModel(
  current_page_num: number,
  page: PageResponse | null,
  singlePageTest: SinglePageTestState = IDLE_SINGLE_PAGE_TEST,
): PagePaneViewModel {
  const normalizedPageNum = normalizeCurrentPageNum(current_page_num);

  if (singlePageTest.status === "running") {
    const pageNum = resolveTestPageNum(singlePageTest.pageNum, normalizedPageNum);
    return {
      current_page_num: pageNum,
      state: "test-running",
      title: `测试中 · 第 ${pageNum} 页`,
      message: `正在对第 ${pageNum} 页执行单页测试，请稍候。`,
      content: null,
      error: null,
    };
  }

  if (singlePageTest.status === "done") {
    const pageNum = resolveTestPageNum(singlePageTest.pageNum, normalizedPageNum);
    return {
      current_page_num: pageNum,
      state: "test-done",
      title: `测试结果 · 第 ${pageNum} 页`,
      message: `第 ${pageNum} 页单页测试完成。`,
      content: singlePageTest.content ?? "",
      error: null,
    };
  }

  if (singlePageTest.status === "failed") {
    const pageNum = resolveTestPageNum(singlePageTest.pageNum, normalizedPageNum);
    const message = singlePageTest.error?.message ?? "单页测试失败。";
    return {
      current_page_num: pageNum,
      state: "test-failed",
      title: `测试失败 · 第 ${pageNum} 页`,
      message,
      content: null,
      error: message,
    };
  }

  if (!page || page.status === "pending") {
    return {
      current_page_num: normalizedPageNum,
      state: "pending",
      title: "等待提取",
      message: "当前页面尚未开始提取。",
      content: null,
      error: null,
    };
  }

  if (page.status === "extracting") {
    return {
      current_page_num: normalizedPageNum,
      state: "extracting",
      title: "提取中",
      message: "当前页面正在提取中，请稍候。",
      content: null,
      error: null,
    };
  }

  if (page.status === "done") {
    return {
      current_page_num: normalizedPageNum,
      state: "done",
      title: "提取完成",
      message: "当前页面提取完成。",
      content: page.content ?? "",
      error: null,
    };
  }

  return {
    current_page_num: normalizedPageNum,
    state: "failed",
    title: "提取失败",
    message: page.error ?? "当前页面提取失败。",
    content: null,
    error: page.error ?? "当前页面提取失败。",
  };
}

export function createPagePaneController(deps: PagePaneControllerDeps): PagePaneController {
  return {
    getViewModel(page: PageResponse | null): PagePaneViewModel {
      const state = deps.workspaceStore.getState();
      const testSnapshot = state.job_id ? IDLE_SINGLE_PAGE_TEST : state.singlePageTest;
      return buildPagePaneViewModel(state.current_page_num, page, testSnapshot);
    },
  };
}
