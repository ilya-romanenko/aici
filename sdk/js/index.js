const DEFAULT_BASE_URL = "https://aici.pro/api/v1";
const DEFAULT_USER_AGENT = "aici-js-sdk/0.1.0";

class AiciError extends Error {
  constructor(message, statusCode) {
    super(message);
    this.name = this.constructor.name;
    this.statusCode = statusCode ?? null;
  }
}

class AiciApiError extends AiciError {
  constructor(statusCode, message, payload) {
    super(message ?? "API error", statusCode);
    this.payload = payload;
  }
}

class AiciAuthenticationError extends AiciApiError {}

class AiciRateLimitError extends AiciApiError {}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const buildUrl = (baseUrl, path) => {
  const normalized = path.startsWith("/") ? path : `/${path}`;
  return new URL(normalized, baseUrl);
};

const normalizeHeaders = (headers) => {
  const result = new Headers();
  Object.entries(headers || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null) {
      result.set(key, value);
    }
  });
  return result;
};

class AiciClient {
  constructor(options = {}) {
    const { apiKey, baseUrl = DEFAULT_BASE_URL, timeout = 30_000, retries = 2, backoffFactor = 500, userAgent = DEFAULT_USER_AGENT } = options;
    if (!apiKey) {
      throw new Error("apiKey is required");
    }
    this.apiKey = apiKey;
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.timeout = timeout;
    this.retries = Math.max(0, Number(retries) || 0);
    this.backoffFactor = Math.max(0, Number(backoffFactor) || 0);
    this.userAgent = userAgent;
  }

  async _request(method, path, { searchParams, body } = {}) {
    const url = buildUrl(this.baseUrl, path);
    if (searchParams) {
      Object.entries(searchParams).forEach(([key, value]) => {
        if (value !== undefined && value !== null) {
          url.searchParams.set(key, value);
        }
      });
    }

    const headers = normalizeHeaders({
      "X-API-Key": this.apiKey,
      "Accept": "application/json",
      "Content-Type": body ? "application/json" : undefined,
      "User-Agent": this.userAgent,
    });

    let lastError;
    for (let attempt = 0; attempt <= this.retries; attempt += 1) {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), this.timeout);
      try {
        const response = await fetch(url, {
          method,
          headers,
          body: body ? JSON.stringify(body) : undefined,
          signal: controller.signal,
        });
        clearTimeout(timeoutId);

        if (response.status >= 400) {
          let payload = null;
          try {
            payload = await response.clone().json();
          } catch {
            payload = await response.clone().text();
          }
          const message = payload?.detail || payload?.message || response.statusText;
          if ([401, 403].includes(response.status)) {
            throw new AiciAuthenticationError(response.status, message, payload);
          }
          if (response.status === 429) {
            throw new AiciRateLimitError(response.status, message, payload);
          }
          if (this._shouldRetry(response.status) && attempt < this.retries) {
            await sleep(this._delay(attempt));
            continue;
          }
          throw new AiciApiError(response.status, message, payload);
        }

        return response.json();
      } catch (error) {
        clearTimeout(timeoutId);
        lastError = error;
        const retryable = error instanceof AiciApiError && this._shouldRetry(error.statusCode);
        if (retryable && attempt < this.retries) {
          await sleep(this._delay(attempt));
          continue;
        }
        if (error.name === "AbortError") {
          throw new AiciError("Request timed out", 408);
        }
        throw error;
      }
    }
    throw new AiciError(`Request failed: ${lastError?.message ?? "unknown error"}`);
  }

  _shouldRetry(statusCode) {
    return [408, 425, 429, 500, 502, 503, 504].includes(statusCode ?? 0);
  }

  _delay(attempt) {
    if (!this.backoffFactor) {
      return 0;
    }
    return this.backoffFactor * 2 ** attempt;
  }

  async getLatestWeights() {
    return this._request("GET", "/weights/latest");
  }

  async getRunWeights(runId) {
    return this._request("GET", `/runs/${runId}/weights`);
  }

  async getRunPerformance(runId) {
    return this._request("GET", `/runs/${runId}/perf`);
  }

  async getIndexComposition() {
    return this._request("GET", "/index-composition");
  }

  async listPerformanceSnapshots() {
    return this._request("GET", "/performance");
  }
}

const createAiciClient = (options) => new AiciClient(options);

export {
  AiciClient,
  AiciApiError,
  AiciAuthenticationError,
  AiciError,
  AiciRateLimitError,
  DEFAULT_BASE_URL,
  createAiciClient,
};
