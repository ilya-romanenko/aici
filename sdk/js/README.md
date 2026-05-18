# AI Crypto Index JavaScript SDK

`@aici/sdk` is a zero-dependency ESM module that signs requests with your API key, retries transient failures, and exposes friendly helpers for the most common allocation endpoints.

## Install

```bash
npm install @aici/sdk@file:../../sdk/js  # from this repository
# or npm install git+ssh://<your-clone-url>#subdirectory=sdk/js
```

The client relies on the Fetch API and `AbortController`. Node.js 18+ ships both by default; older runtimes require a ponyfill such as `undici`.

## Usage

```js
import { createAiciClient } from "@aici/sdk";

const client = createAiciClient({
  apiKey: process.env.AICI_API_KEY,
  baseUrl: process.env.AICI_BASE_URL ?? "https://aici.pro/api/v1",
});
const weights = await client.getLatestWeights();
console.log(weights.items.slice(0, 5));
```

See `examples/sdk_js_quickstart` for a runnable Node.js example that prints weights and Sharpe ratios.

## API

```ts
interface AiciClientOptions {
  apiKey: string;
  baseUrl?: string;      // defaults to https://aici.pro/api/v1
  timeout?: number;      // per request timeout in ms
  retries?: number;      // retry attempts for 429/5xx
  backoffFactor?: number;// exponential delay multiplier
}
```

Authentication and environment:
- Store `AICI_API_KEY` in your `.env` or runtime secrets; each key is bound to an IP allow list (403 when not matched).
- Default base URL points to production. Override `baseUrl` (or set `AICI_BASE_URL`) for staging.
- Sandbox keys ship with delayed data and conservative quotas until you request production access in `/app`.

Methods:

- `getLatestWeights()` → `{ run_id, items: [{ asset, weight }, ...] }`
- `getRunWeights(runId)` → same as above for a historical run.
- `getRunPerformance(runId)` → `{ run_id, metrics: Record<string, number> }`
- `getIndexComposition()` → `{ run_id, summary, assets }`
- `listPerformanceSnapshots()` → `{ defaultKey, snapshots: Record<string, PerformanceSnapshot> }`

Rate limits and retries:
- Default throttle is roughly 120 requests per 60 seconds on sandbox keys; responses expose `X-RateLimit-*` and `Retry-After`.
- The SDK retries `429`/`5xx` with exponential backoff. Avoid parallel floods that exceed your burst window.

Errors inherit from `AiciError`; catch `AiciAuthenticationError` for 401/403 and `AiciRateLimitError` for throttling feedback.
