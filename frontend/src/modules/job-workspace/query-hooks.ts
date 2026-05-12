import type {
  ConfigApiClient,
  JobApiClient,
} from "../../shared/api/index.ts";
import type {
  JobResponse,
  OutputDocumentResponse,
  PageResponse,
  PageSummaryResponse,
  PublicConfigResponse,
} from "../../shared/types/index.ts";

export type QueryKey = readonly [string, ...unknown[]];

export interface QueryDescriptor<TValue> {
  queryKey: QueryKey;
  queryFn: () => Promise<TValue>;
}

export interface WorkspaceQueryHookDeps {
  jobApiClient: Pick<JobApiClient, "getJob" | "listPages" | "getPage" | "getOutput">;
  configApiClient: Pick<ConfigApiClient, "getPublicConfig">;
}

export interface WorkspaceQueryHooks {
  useJobQuery(job_id: string): QueryDescriptor<JobResponse>;
  usePagesQuery(job_id: string): QueryDescriptor<PageSummaryResponse[]>;
  usePageQuery(job_id: string, page_num: number): QueryDescriptor<PageResponse>;
  useOutputQuery(job_id: string): QueryDescriptor<OutputDocumentResponse>;
  useConfigQuery(): QueryDescriptor<PublicConfigResponse>;
}

export const workspaceQueryKeys = {
  job: (job_id: string) => ["job", job_id] as const,
  pages: (job_id: string) => ["job-pages", job_id] as const,
  page: (job_id: string, page_num: number) => ["job-page", job_id, page_num] as const,
  output: (job_id: string) => ["job-output", job_id] as const,
  config: () => ["config"] as const,
};

export function createWorkspaceQueryHooks(deps: WorkspaceQueryHookDeps): WorkspaceQueryHooks {
  return {
    useJobQuery(job_id: string): QueryDescriptor<JobResponse> {
      return {
        queryKey: workspaceQueryKeys.job(job_id),
        queryFn: () => deps.jobApiClient.getJob(job_id),
      };
    },
    usePagesQuery(job_id: string): QueryDescriptor<PageSummaryResponse[]> {
      return {
        queryKey: workspaceQueryKeys.pages(job_id),
        queryFn: () => deps.jobApiClient.listPages(job_id),
      };
    },
    usePageQuery(job_id: string, page_num: number): QueryDescriptor<PageResponse> {
      return {
        queryKey: workspaceQueryKeys.page(job_id, page_num),
        queryFn: () => deps.jobApiClient.getPage(job_id, page_num),
      };
    },
    useOutputQuery(job_id: string): QueryDescriptor<OutputDocumentResponse> {
      return {
        queryKey: workspaceQueryKeys.output(job_id),
        queryFn: () => deps.jobApiClient.getOutput(job_id),
      };
    },
    useConfigQuery(): QueryDescriptor<PublicConfigResponse> {
      return {
        queryKey: workspaceQueryKeys.config(),
        queryFn: () => deps.configApiClient.getPublicConfig(),
      };
    },
  };
}
