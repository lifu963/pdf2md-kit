"""
Step 23: 建立前端应用壳、统一客户端与状态归属

验收目标（严格对齐实施步骤）：
1. 首页与 `/jobs/{id}` 路由可挂载。
2. `JobApiClient` / `ConfigApiClient` 路径、参数正确，且错误可透传。
3. `JobStreamClient` 可建立连接并正确释放。
4. store 只保存 `job_id`、`current_page_num`、`workspace_mode`、`is_building_busy`。
5. query hooks 的 query key 与架构文档约束一致。
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[2]


def _run_node_script(script: str) -> dict[str, object]:
    result = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "--eval", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            "Node 断言脚本失败:\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

    payload = (result.stdout or "").strip()
    if not payload:
        return {}
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"Node 输出不是 JSON: {payload}") from exc


class Step23FrontendAppShellClientAndStoreTests(TestCase):
    def test_shared_stream_types_allow_extracting_page_events(self) -> None:
        source = (ROOT / "frontend/src/shared/types/index.ts").read_text(encoding="utf-8")
        self.assertIn(
            'export interface PageEventPayload {\n  type: "page";\n  page_num: number;\n  status: PageStatus;',
            source,
        )

    def test_route_shell_mounts_home_and_job_workspace_paths(self) -> None:
        result = _run_node_script(
            """
import path from "node:path";
import { pathToFileURL } from "node:url";

const shellModule = await import(pathToFileURL(path.resolve("frontend/src/app/shell.ts")).href);
const queryModule = await import(pathToFileURL(path.resolve("frontend/src/modules/job-workspace/query-hooks.ts")).href);

const shell = shellModule.createAppRouterShell();
const home = shell.mount("/");
const job = shell.mount("/jobs/aaaaaaaa-bbbb-cccc-dddd-000000000023");
const unknown = shell.mount("/settings");
const pageKey = queryModule.workspaceQueryKeys.page("job-x", 3);

if (home.route !== "home") {
  throw new Error(`expected home route, got ${JSON.stringify(home)}`);
}
if (job.route !== "job-workspace" || job.job_id !== "aaaaaaaa-bbbb-cccc-dddd-000000000023") {
  throw new Error(`expected job route, got ${JSON.stringify(job)}`);
}
if (unknown.route !== "not-found") {
  throw new Error(`expected not-found route, got ${JSON.stringify(unknown)}`);
}
if (JSON.stringify(pageKey) !== JSON.stringify(["job-page", "job-x", 3])) {
  throw new Error(`unexpected page query key: ${JSON.stringify(pageKey)}`);
}

console.log(JSON.stringify({ home, job, unknown, pageKey }));
"""
        )
        self.assertEqual("home", result["home"]["route"])
        self.assertEqual("job-workspace", result["job"]["route"])
        self.assertEqual("not-found", result["unknown"]["route"])

    def test_api_clients_use_expected_paths_and_propagate_http_errors(self) -> None:
        result = _run_node_script(
            """
import path from "node:path";
import { pathToFileURL } from "node:url";

const apiModule = await import(pathToFileURL(path.resolve("frontend/src/shared/api/index.ts")).href);
const { JobApiClient, ConfigApiClient, ApiClientError } = apiModule;

const calls = [];
const jsonResponse = (payload, status = 200) =>
  new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });

const fetcher = async (input, init = {}) => {
  const method = (init.method ?? "GET").toUpperCase();
  const url = String(input);
  calls.push({
    method,
    url,
    bodyType: init.body ? (init.body.constructor?.name ?? typeof init.body) : "undefined",
    bodyText: typeof init.body === "string" ? init.body : null,
  });

  if (url.endsWith("/api/jobs/error-job")) {
    return jsonResponse(
      {
        detail: {
          code: "JOB_NOT_FOUND",
          message: "job missing",
          details: { job_id: "error-job" },
        },
      },
      404,
    );
  }

  if (
    url.endsWith("/api/config") &&
    method === "PUT" &&
    typeof init.body === "string" &&
    init.body.includes('"name":"bad-model"')
  ) {
    return jsonResponse(
      {
        detail: {
          code: "CONFIG_INVALID",
          message: "invalid config",
          details: { field: "model.name" },
        },
      },
      400,
    );
  }

  if (url.endsWith("/api/jobs") && method === "POST") {
    return jsonResponse({ job_id: "job-1", total_pages: 4, status: "extracting" });
  }
  if (url.endsWith("/api/jobs/job-1") && method === "GET") {
    return jsonResponse({
      job_id: "job-1",
      status: "extracting",
      total_pages: 4,
      succeeded_pages: [1],
      failed_pages: [],
      processed_count: 1,
    });
  }
  if (url.endsWith("/api/jobs/job-1/pages") && method === "GET") {
    return jsonResponse([{ page_num: 1, status: "done" }]);
  }
  if (url.endsWith("/api/jobs/job-1/pages/1") && method === "GET") {
    return jsonResponse({ page_num: 1, status: "done", content: "# page-1" });
  }
  if (url.endsWith("/api/jobs/job-1/pages/1") && method === "PUT") {
    return jsonResponse({ page_num: 1, status: "done", content: "# edited" });
  }
  if (url.endsWith("/api/jobs/job-1/pages/1/retry") && method === "POST") {
    return jsonResponse({ job_id: "job-1", page_num: 1 });
  }
  if (url.endsWith("/api/jobs/job-1/build") && method === "POST") {
    return jsonResponse({
      status: "ready",
      output_url: "/api/jobs/job-1/output",
      download_url: "/api/jobs/job-1/output/download",
    });
  }
  if (url.endsWith("/api/jobs/job-1/output") && method === "GET") {
    return jsonResponse({ content: "# output", updated_at: "2026-04-08T12:00:00Z" });
  }
  if (url.endsWith("/api/jobs/job-1/output") && method === "PUT") {
    return jsonResponse({ content: "# saved", updated_at: "2026-04-08T12:01:00Z" });
  }
  if (url.endsWith("/api/jobs/job-1/output/discard") && method === "POST") {
    return jsonResponse({
      job_id: "job-1",
      status: "extracted",
      total_pages: 4,
      succeeded_pages: [1, 2, 3, 4],
      failed_pages: [],
      processed_count: 4,
    });
  }
  if (url.endsWith("/api/config") && method === "GET") {
    return jsonResponse({
      model: { name: "vision", timeout: 60 },
      extract: { dpi: 180, concurrency: 4, max_retries: 2, prompt: "prompt" },
      has_api_key: true,
    });
  }
  if (url.endsWith("/api/config") && method === "PUT") {
    return jsonResponse({
      model: { name: "vision", timeout: 60 },
      extract: { dpi: 200, concurrency: 3, max_retries: 1, prompt: "updated" },
      has_api_key: true,
    });
  }
  if (url.endsWith("/api/config/reset") && method === "POST") {
    return jsonResponse({
      model: { name: "vision-template", timeout: 30 },
      extract: { dpi: 150, concurrency: 2, max_retries: 1, prompt: "prompt" },
      has_api_key: true,
    });
  }
  if (url.endsWith("/api/config/test-connection") && method === "POST") {
    return jsonResponse({
      ok: true,
      message: "LLM API 响应正常",
      reply_preview: "OK",
    });
  }

  return jsonResponse(
    { detail: { code: "UNEXPECTED_ERROR", message: `unexpected route: ${method} ${url}` } },
    500,
  );
};

const jobClient = new JobApiClient({ baseUrl: "http://localhost:8000", fetcher });
const configClient = new ConfigApiClient({ baseUrl: "http://localhost:8000", fetcher });

await jobClient.createJob({
  filename: "demo.pdf",
  pdf: new Blob(["pdf-binary"], { type: "application/pdf" }),
});
await jobClient.getJob("job-1");
await jobClient.listPages("job-1");
await jobClient.getPage("job-1", 1);
await jobClient.savePage("job-1", 1, "# edited");
await jobClient.retryPage("job-1", 1);
await jobClient.buildOutput("job-1");
await jobClient.getOutput("job-1");
await jobClient.saveOutput("job-1", "# saved");
await jobClient.discardOutput("job-1");
await configClient.getPublicConfig();
await configClient.updateConfig({
  model: { name: "vision", timeout: 60 },
  extract: { dpi: 200, concurrency: 3, max_retries: 1, prompt: "updated" },
});
const restoredConfig = await configClient.restoreInitialConfig();
const connectionResult = await configClient.testConnection();

const sourceUrl = jobClient.getSourceDocumentUrl("job-1");
if (sourceUrl !== "http://localhost:8000/api/jobs/job-1/source") {
  throw new Error(`unexpected source url: ${sourceUrl}`);
}

let jobError = null;
try {
  await jobClient.getJob("error-job");
} catch (error) {
  jobError = error;
}
if (!(jobError instanceof ApiClientError)) {
  throw new Error(`expected ApiClientError for job error, got ${String(jobError)}`);
}
if (jobError.status !== 404 || jobError.code !== "JOB_NOT_FOUND") {
  throw new Error(`unexpected job error payload: ${JSON.stringify(jobError)}`);
}
if (jobError.details?.job_id !== "error-job") {
  throw new Error(`missing job_id in error details: ${JSON.stringify(jobError.details)}`);
}

let configError = null;
try {
  await configClient.updateConfig({
    model: { name: "bad-model", timeout: 60 },
    extract: { dpi: 200, concurrency: 3, max_retries: 1, prompt: "updated" },
  });
} catch (error) {
  configError = error;
}
if (!(configError instanceof ApiClientError)) {
  throw new Error(`expected ApiClientError for config error, got ${String(configError)}`);
}
if (configError.status !== 400 || configError.code !== "CONFIG_INVALID") {
  throw new Error(`unexpected config error payload: ${JSON.stringify(configError)}`);
}
if (configError.details?.field !== "model.name") {
  throw new Error(`missing field in config error details: ${JSON.stringify(configError.details)}`);
}

const savePageCall = calls.find((item) => item.url.endsWith("/api/jobs/job-1/pages/1") && item.method === "PUT");
const createJobCall = calls.find((item) => item.url.endsWith("/api/jobs") && item.method === "POST");

if (!savePageCall || !savePageCall.bodyText || !savePageCall.bodyText.includes('"content":"# edited"')) {
  throw new Error(`save page call body mismatch: ${JSON.stringify(savePageCall)}`);
}
if (!createJobCall || createJobCall.bodyType !== "FormData") {
  throw new Error(`create job call should use FormData: ${JSON.stringify(createJobCall)}`);
}

console.log(
  JSON.stringify({
    callCount: calls.length,
    firstCall: calls[0],
    lastCall: calls[calls.length - 1],
    connectionResult,
    restoredConfig,
    savePageCall,
    createJobCall,
  }),
);
"""
        )
        self.assertGreaterEqual(int(result["callCount"]), 15)
        self.assertEqual("POST", result["firstCall"]["method"])
        self.assertTrue(str(result["firstCall"]["url"]).endswith("/api/jobs"))
        self.assertEqual("LLM API 响应正常", result["connectionResult"]["message"])
        self.assertEqual("vision-template", result["restoredConfig"]["model"]["name"])

    def test_stream_client_connects_receives_events_and_releases_subscription(self) -> None:
        result = _run_node_script(
            """
import path from "node:path";
import { pathToFileURL } from "node:url";

const streamModule = await import(pathToFileURL(path.resolve("frontend/src/shared/stream/index.ts")).href);
const { JobStreamClient } = streamModule;

class FakeEventSource {
  constructor(url) {
    this.url = url;
    this.closed = false;
    this.listeners = new Map();
  }

  addEventListener(type, listener) {
    const list = this.listeners.get(type) ?? [];
    list.push(listener);
    this.listeners.set(type, list);
  }

  removeEventListener(type, listener) {
    const list = this.listeners.get(type) ?? [];
    this.listeners.set(
      type,
      list.filter((item) => item !== listener),
    );
  }

  close() {
    this.closed = true;
  }

  emit(type, event) {
    const list = this.listeners.get(type) ?? [];
    for (const listener of list) {
      listener(event);
    }
  }
}

const openedSources = [];
const receivedEvents = [];
const receivedErrors = [];
let openedCount = 0;

const streamClient = new JobStreamClient({
  baseUrl: "http://localhost:8000",
  eventSourceFactory: (url) => {
    const source = new FakeEventSource(url);
    openedSources.push(source);
    return source;
  },
});

const subscription = streamClient.subscribeJobEvents("job-stream-1", {
  onOpen: () => {
    openedCount += 1;
  },
  onEvent: (payload) => {
    receivedEvents.push(payload);
  },
  onError: (error) => {
    receivedErrors.push(String(error));
  },
});

const source = openedSources[0];
source.emit("open", { type: "open" });
source.emit("message", {
  data: JSON.stringify({
    type: "page",
    page_num: 1,
    status: "done",
    processed_count: 1,
    total_pages: 4,
  }),
});
source.emit("message", {
  data: JSON.stringify({
    type: "complete",
    processed_count: 4,
    total_pages: 4,
    succeeded_pages: [1, 2, 3, 4],
    failed_pages: [],
  }),
});
source.emit("message", { data: "not-json" });
source.emit("error", { message: "network down" });

subscription.close();
subscription.close();

if (!source.closed) {
  throw new Error("subscription.close() must close EventSource");
}
if (openedCount !== 1) {
  throw new Error(`expected 1 open callback, got ${openedCount}`);
}
if (receivedEvents.length !== 2) {
  throw new Error(`expected 2 events, got ${receivedEvents.length}`);
}
if (receivedErrors.length < 2) {
  throw new Error(`expected parse + network errors, got ${receivedErrors.length}`);
}

console.log(
  JSON.stringify({
    streamUrl: source.url,
    openedCount,
    eventCount: receivedEvents.length,
    errorCount: receivedErrors.length,
    lastEvent: receivedEvents[receivedEvents.length - 1],
  }),
);
"""
        )
        self.assertTrue(str(result["streamUrl"]).endswith("/api/jobs/job-stream-1/stream"))
        self.assertEqual(2, result["eventCount"])
        self.assertGreaterEqual(int(result["errorCount"]), 2)

    def test_workspace_store_state_boundary_and_query_hook_keys_are_stable(self) -> None:
        result = _run_node_script(
            """
import path from "node:path";
import { pathToFileURL } from "node:url";

const storeModule = await import(pathToFileURL(path.resolve("frontend/src/modules/job-workspace/store.ts")).href);
const queryModule = await import(pathToFileURL(path.resolve("frontend/src/modules/job-workspace/query-hooks.ts")).href);
const appQueryModule = await import(pathToFileURL(path.resolve("frontend/src/app/query-container.ts")).href);

const store = storeModule.createWorkspaceStore({
  job_id: "job-1",
  current_page_num: 2,
  workspace_mode: "page",
  is_building_busy: false,
});

const stateKeys = Object.keys(store.getState()).sort();
const expectedKeys = ["current_page_num", "is_building_busy", "job_id", "singlePageTest", "workspace_mode"];
if (JSON.stringify(stateKeys) !== JSON.stringify(expectedKeys)) {
  throw new Error(`workspace store state keys drifted: ${JSON.stringify(stateKeys)}`);
}
const initialSinglePageTest = store.getState().singlePageTest;
if (
  !initialSinglePageTest
  || initialSinglePageTest.status !== "idle"
  || initialSinglePageTest.pageNum !== null
  || initialSinglePageTest.content !== null
  || initialSinglePageTest.error !== null
) {
  throw new Error(`initial singlePageTest snapshot drifted: ${JSON.stringify(initialSinglePageTest)}`);
}

let notifyCount = 0;
const unsubscribe = store.subscribe(() => {
  notifyCount += 1;
});
store.setState({ current_page_num: 3 });
unsubscribe();
store.setState({ current_page_num: 4 });

if (notifyCount != 1) {
  throw new Error(`expected one subscription notification, got ${notifyCount}`);
}

const calls = [];
const fakeJobApiClient = {
  async getJob(jobId) {
    calls.push(`getJob:${jobId}`);
    return { job_id: jobId, status: "extracting", total_pages: 4, succeeded_pages: [], failed_pages: [], processed_count: 0 };
  },
  async listPages(jobId) {
    calls.push(`listPages:${jobId}`);
    return [{ page_num: 1, status: "pending" }];
  },
  async getPage(jobId, pageNum) {
    calls.push(`getPage:${jobId}:${pageNum}`);
    return { page_num: pageNum, status: "pending" };
  },
  async getOutput(jobId) {
    calls.push(`getOutput:${jobId}`);
    return { content: "", updated_at: "2026-04-08T00:00:00Z" };
  },
};
const fakeConfigApiClient = {
  async getPublicConfig() {
    calls.push("getPublicConfig");
    return {
      model: { name: "vision", timeout: 60 },
      extract: { dpi: 180, concurrency: 4, max_retries: 2, prompt: "prompt" },
      has_api_key: true,
    };
  },
};

const hooks = queryModule.createWorkspaceQueryHooks({
  jobApiClient: fakeJobApiClient,
  configApiClient: fakeConfigApiClient,
});
const container = appQueryModule.createAppQueryContainer({
  jobApiClient: fakeJobApiClient,
  configApiClient: fakeConfigApiClient,
});

const keyJob = hooks.useJobQuery("job-1").queryKey;
const keyPages = hooks.usePagesQuery("job-1").queryKey;
const keyPage = hooks.usePageQuery("job-1", 9).queryKey;
const keyOutput = hooks.useOutputQuery("job-1").queryKey;
const keyConfig = hooks.useConfigQuery().queryKey;
const keyContainerPage = container.jobWorkspace.usePageQuery("job-1", 5).queryKey;

if (JSON.stringify(keyJob) !== JSON.stringify(["job", "job-1"])) {
  throw new Error(`job query key mismatch: ${JSON.stringify(keyJob)}`);
}
if (JSON.stringify(keyPages) !== JSON.stringify(["job-pages", "job-1"])) {
  throw new Error(`pages query key mismatch: ${JSON.stringify(keyPages)}`);
}
if (JSON.stringify(keyPage) !== JSON.stringify(["job-page", "job-1", 9])) {
  throw new Error(`page query key mismatch: ${JSON.stringify(keyPage)}`);
}
if (JSON.stringify(keyOutput) !== JSON.stringify(["job-output", "job-1"])) {
  throw new Error(`output query key mismatch: ${JSON.stringify(keyOutput)}`);
}
if (JSON.stringify(keyConfig) !== JSON.stringify(["config"])) {
  throw new Error(`config query key mismatch: ${JSON.stringify(keyConfig)}`);
}
if (JSON.stringify(keyContainerPage) !== JSON.stringify(["job-page", "job-1", 5])) {
  throw new Error(`container page query key mismatch: ${JSON.stringify(keyContainerPage)}`);
}

await hooks.useJobQuery("job-1").queryFn();
await hooks.usePagesQuery("job-1").queryFn();
await hooks.usePageQuery("job-1", 9).queryFn();
await hooks.useOutputQuery("job-1").queryFn();
await hooks.useConfigQuery().queryFn();

console.log(
  JSON.stringify({
    state: store.getState(),
    notifyCount,
    calls,
    keyPage,
    keyConfig,
  }),
);
"""
        )
        self.assertEqual("job-1", result["state"]["job_id"])
        self.assertEqual(4, result["state"]["current_page_num"])
        self.assertEqual("config", result["keyConfig"][0])

    def test_single_page_preview_client_posts_multipart_and_propagates_errors(self) -> None:
        result = _run_node_script(
            """
import path from "node:path";
import { pathToFileURL } from "node:url";

const apiModule = await import(pathToFileURL(path.resolve("frontend/src/shared/api/index.ts")).href);
const { postSinglePagePreview, ApiClientError } = apiModule;

if (typeof postSinglePagePreview !== "function") {
  throw new Error("postSinglePagePreview must be exported as a function");
}
if (postSinglePagePreview.length !== 2) {
  throw new Error(`postSinglePagePreview must accept 2 required params, got arity ${postSinglePagePreview.length}`);
}

const calls = [];
const jsonResponse = (payload, status = 200) =>
  new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });

const fetcher = async (input, init = {}) => {
  const method = (init.method ?? "GET").toUpperCase();
  const url = String(input);
  const body = init.body;
  let fileEntry = null;
  let pageNumEntry = null;
  let formDataKeys = null;
  if (body && body.constructor?.name === "FormData") {
    formDataKeys = Array.from(body.keys()).sort();
    const fileValue = body.get("file");
    const pageNumValue = body.get("page_num");
    if (fileValue instanceof Blob) {
      fileEntry = {
        constructorName: fileValue.constructor?.name ?? null,
        size: fileValue.size,
        type: fileValue.type,
        name: typeof fileValue.name === "string" ? fileValue.name : null,
      };
    }
    pageNumEntry = typeof pageNumValue === "string" ? pageNumValue : null;
  }
  calls.push({
    method,
    url,
    bodyType: body ? (body.constructor?.name ?? typeof body) : "undefined",
    hasSignal: Boolean(init.signal),
    formDataKeys,
    fileEntry,
    pageNumEntry,
  });

  if (url.endsWith("/api/extraction/single-page-preview") && method === "POST") {
    if (pageNumEntry === "42") {
      return jsonResponse(
        {
          detail: {
            code: "PAGE_NOT_FOUND",
            message: "page 42 is out of range",
            details: { page_num: 42 },
          },
        },
        404,
      );
    }
    return jsonResponse({ page_num: Number(pageNumEntry), content: "## preview" });
  }

  return jsonResponse(
    { detail: { code: "UNEXPECTED_ERROR", message: `unexpected route: ${method} ${url}` } },
    500,
  );
};

const file = new File(["%PDF-1.4 fake"], "sample.pdf", { type: "application/pdf" });
const abortController = new AbortController();
const happy = await postSinglePagePreview(file, 2, {
  baseUrl: "http://localhost:8000",
  fetcher,
  signal: abortController.signal,
});
if (happy.page_num !== 2 || happy.content !== "## preview") {
  throw new Error(`unexpected happy payload: ${JSON.stringify(happy)}`);
}

let errorCaught = null;
try {
  await postSinglePagePreview(file, 42, {
    baseUrl: "http://localhost:8000",
    fetcher,
  });
} catch (error) {
  errorCaught = error;
}
if (!(errorCaught instanceof ApiClientError)) {
  throw new Error(`expected ApiClientError, got ${String(errorCaught)}`);
}
if (errorCaught.status !== 404 || errorCaught.code !== "PAGE_NOT_FOUND") {
  throw new Error(`unexpected error shape: ${JSON.stringify({status: errorCaught.status, code: errorCaught.code})}`);
}
if (errorCaught.details?.page_num !== 42) {
  throw new Error(`missing details.page_num: ${JSON.stringify(errorCaught.details)}`);
}

let validationCaught = null;
try {
  await postSinglePagePreview(file, 0, {
    baseUrl: "http://localhost:8000",
    fetcher,
  });
} catch (error) {
  validationCaught = error;
}
if (!validationCaught || !(validationCaught instanceof Error)) {
  throw new Error("expected validation error for page_num=0");
}

const postCall = calls[0];
if (postCall.method !== "POST") {
  throw new Error(`expected POST, got ${postCall.method}`);
}
if (!postCall.url.endsWith("/api/extraction/single-page-preview")) {
  throw new Error(`unexpected url: ${postCall.url}`);
}
if (postCall.bodyType !== "FormData") {
  throw new Error(`expected FormData body, got ${postCall.bodyType}`);
}
if (JSON.stringify(postCall.formDataKeys) !== JSON.stringify(["file", "page_num"])) {
  throw new Error(`unexpected form keys: ${JSON.stringify(postCall.formDataKeys)}`);
}
if (postCall.pageNumEntry !== "2") {
  throw new Error(`expected page_num='2', got ${postCall.pageNumEntry}`);
}
if (!postCall.fileEntry || postCall.fileEntry.name !== "sample.pdf") {
  throw new Error(`file entry missing name: ${JSON.stringify(postCall.fileEntry)}`);
}
if (!postCall.hasSignal) {
  throw new Error("signal should be threaded through to fetcher init");
}

console.log(
  JSON.stringify({
    callCount: calls.length,
    happy,
    postCall,
  }),
);
"""
        )
        self.assertEqual(2, int(result["callCount"]))
        self.assertEqual(2, int(result["happy"]["page_num"]))
        self.assertEqual("## preview", result["happy"]["content"])
        self.assertEqual("FormData", result["postCall"]["bodyType"])

