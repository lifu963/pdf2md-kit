import {
  createWorkspaceQueryHooks,
  type WorkspaceQueryHookDeps,
  type WorkspaceQueryHooks,
} from "../modules/job-workspace/query-hooks.ts";

export interface AppQueryContainer {
  jobWorkspace: WorkspaceQueryHooks;
}

export function createAppQueryContainer(deps: WorkspaceQueryHookDeps): AppQueryContainer {
  return {
    jobWorkspace: createWorkspaceQueryHooks(deps),
  };
}
