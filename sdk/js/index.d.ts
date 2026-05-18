export interface WeightEntry {
  asset: string;
  weight: number;
}

export interface WeightsSnapshot {
  run_id: string;
  items: WeightEntry[];
}

export interface RunPerformance {
  run_id: string;
  metrics: Record<string, number>;
}

export interface IndexCompositionSummary {
  count: number;
  top3_pct: number;
  herfindahl: number;
  effective_assets?: number | null;
  max_weight_pct: number;
  min_weight_pct: number;
  mean_weight_pct: number;
  total_weight_pct: number;
}

export interface IndexCompositionAsset {
  rank: number;
  asset: string;
  weight_pct: number;
  weight: number;
  cumulative_pct: number;
  relative_to_mean: number;
}

export interface IndexComposition {
  run_id: string;
  updated_display: string;
  updated_iso: string;
  assets: IndexCompositionAsset[];
  summary: IndexCompositionSummary;
}

export interface PerformanceSnapshot {
  strategy_key: string;
  strategy_label: string;
  chart_caption: string;
  chart_period_label: string;
  metric_cards: Array<Record<string, string>>;
}

export interface PerformanceResponse {
  default_key: string;
  snapshots: Record<string, PerformanceSnapshot>;
}

export interface AiciClientOptions {
  apiKey: string;
  baseUrl?: string;
  timeout?: number;
  retries?: number;
  backoffFactor?: number;
  userAgent?: string;
}

export declare class AiciError extends Error {
  statusCode: number | null;
}

export declare class AiciApiError extends AiciError {
  payload?: unknown;
}

export declare class AiciAuthenticationError extends AiciApiError {}

export declare class AiciRateLimitError extends AiciApiError {}

export declare class AiciClient {
  constructor(options: AiciClientOptions);

  getLatestWeights(): Promise<WeightsSnapshot>;
  getRunWeights(runId: string): Promise<WeightsSnapshot>;
  getRunPerformance(runId: string): Promise<RunPerformance>;
  getIndexComposition(): Promise<IndexComposition>;
  listPerformanceSnapshots(): Promise<PerformanceResponse>;
}

export declare function createAiciClient(options: AiciClientOptions): AiciClient;

export declare const DEFAULT_BASE_URL: string;
