from __future__ import annotations

import os

import uvicorn


def main() -> None:
    # Enable hot reload in development when AICI_DEV is set
    dev_mode = os.getenv("AICI_DEV") in {"1", "true", "True"}
    uvicorn.run(
        "ai_crypto_index.api.app:app",
        host="0.0.0.0",
        port=8000,
        reload=dev_mode,
    )


if __name__ == "__main__":
    main()
