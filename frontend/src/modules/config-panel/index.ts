import type { ConfigApiClient } from "../../shared/api/index.ts";
import type {
  ConfigModelPayload,
  ExtractConfigPayload,
  PublicConfigResponse,
  TestConnectionResponse,
  UpdateConfigRequest,
} from "../../shared/types/index.ts";

type ConfigPanelApiClient = Pick<
  ConfigApiClient,
  "getPublicConfig" | "updateConfig" | "restoreInitialConfig" | "testConnection"
>;

export interface ConfigPanelViewModel {
  model: ConfigModelPayload;
  extract: ExtractConfigPayload;
  has_api_key: boolean;
  api_key: string;
  api_key_placeholder: string;
}

export interface ConfigPanelControllerDeps {
  configApiClient: ConfigPanelApiClient;
}

export interface ConfigPanelConnectionTestViewModel {
  ok: boolean;
  message: string;
  reply_preview?: string;
}

export interface ConfigPanelSaveAndTestResult {
  config: ConfigPanelViewModel;
  connection_test: ConfigPanelConnectionTestViewModel;
}

export interface ConfigPanelController {
  load(): Promise<ConfigPanelViewModel>;
  save(request: UpdateConfigRequest): Promise<ConfigPanelViewModel>;
  restoreInitial(): Promise<ConfigPanelViewModel>;
  saveAndTest(request: UpdateConfigRequest): Promise<ConfigPanelSaveAndTestResult>;
  getViewModel(): ConfigPanelViewModel | null;
}

function cloneModelPayload(model: ConfigModelPayload): ConfigModelPayload {
  return { ...model };
}

function cloneExtractPayload(extract: ExtractConfigPayload): ExtractConfigPayload {
  return { ...extract };
}

function cloneViewModel(view: ConfigPanelViewModel): ConfigPanelViewModel {
  return {
    model: cloneModelPayload(view.model),
    extract: cloneExtractPayload(view.extract),
    has_api_key: view.has_api_key,
    api_key: view.api_key,
    api_key_placeholder: view.api_key_placeholder,
  };
}

function cloneConnectionTestViewModel(
  view: ConfigPanelConnectionTestViewModel,
): ConfigPanelConnectionTestViewModel {
  return { ...view };
}

function toViewModel(config: PublicConfigResponse): ConfigPanelViewModel {
  return {
    model: cloneModelPayload(config.model),
    extract: cloneExtractPayload(config.extract),
    has_api_key: config.has_api_key,
    api_key: "",
    api_key_placeholder: config.has_api_key ? "已配置，留空则保持不变" : "输入 API Key",
  };
}

function toConnectionTestViewModel(
  response: TestConnectionResponse,
): ConfigPanelConnectionTestViewModel {
  return {
    ok: response.ok,
    message: response.message,
    reply_preview: response.reply_preview,
  };
}

function normalizeApiKey(value: string | undefined): string | undefined {
  if (typeof value !== "string") {
    return undefined;
  }
  const trimmed = value.trim();
  return trimmed ? trimmed : undefined;
}

function normalizeUpdateRequest(request: UpdateConfigRequest): UpdateConfigRequest {
  const apiKey = normalizeApiKey(request.api_key);
  const normalizedRequest = {
    model: cloneModelPayload(request.model),
    extract: cloneExtractPayload(request.extract),
  } as UpdateConfigRequest;

  if (apiKey !== undefined) {
    normalizedRequest.api_key = apiKey;
  }
  return normalizedRequest;
}

export function createConfigPanelController(
  deps: ConfigPanelControllerDeps,
): ConfigPanelController {
  let cachedViewModel: ConfigPanelViewModel | null = null;

  return {
    async load(): Promise<ConfigPanelViewModel> {
      const config = await deps.configApiClient.getPublicConfig();
      cachedViewModel = toViewModel(config);
      return cloneViewModel(cachedViewModel);
    },

    async save(request: UpdateConfigRequest): Promise<ConfigPanelViewModel> {
      const updated = await deps.configApiClient.updateConfig(normalizeUpdateRequest(request));
      cachedViewModel = toViewModel(updated);
      return cloneViewModel(cachedViewModel);
    },

    async restoreInitial(): Promise<ConfigPanelViewModel> {
      const restored = await deps.configApiClient.restoreInitialConfig();
      cachedViewModel = toViewModel(restored);
      return cloneViewModel(cachedViewModel);
    },

    async saveAndTest(request: UpdateConfigRequest): Promise<ConfigPanelSaveAndTestResult> {
      const updated = await deps.configApiClient.updateConfig(normalizeUpdateRequest(request));
      cachedViewModel = toViewModel(updated);
      const testResult = toConnectionTestViewModel(
        await deps.configApiClient.testConnection(),
      );
      return {
        config: cloneViewModel(cachedViewModel),
        connection_test: cloneConnectionTestViewModel(testResult),
      };
    },

    getViewModel(): ConfigPanelViewModel | null {
      return cachedViewModel ? cloneViewModel(cachedViewModel) : null;
    },
  };
}
