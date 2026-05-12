export type AppRoute =
  | { route: "home" }
  | { route: "job-workspace"; job_id: string }
  | { route: "not-found"; pathname: string };

export interface AppRouterShell {
  mount(pathname: string): AppRoute;
}

const JOB_ROUTE_PATTERN = /^\/jobs\/([^/?#]+)\/?$/;

function normalizePathname(pathname: string): string {
  const trimmed = pathname.trim();
  if (!trimmed) {
    return "/";
  }
  const withLeadingSlash = trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
  const pathOnly = withLeadingSlash.split("?")[0]?.split("#")[0] ?? "/";
  if (!pathOnly) {
    return "/";
  }
  if (pathOnly.length > 1 && pathOnly.endsWith("/")) {
    return pathOnly.slice(0, -1);
  }
  return pathOnly;
}

export function resolveAppRoute(pathname: string): AppRoute {
  const normalizedPath = normalizePathname(pathname);
  if (normalizedPath === "/") {
    return { route: "home" };
  }

  const match = normalizedPath.match(JOB_ROUTE_PATTERN);
  if (match && match[1]) {
    return { route: "job-workspace", job_id: decodeURIComponent(match[1]) };
  }

  return { route: "not-found", pathname: normalizedPath };
}

export function createAppRouterShell(): AppRouterShell {
  return {
    mount(pathname: string): AppRoute {
      return resolveAppRoute(pathname);
    },
  };
}
