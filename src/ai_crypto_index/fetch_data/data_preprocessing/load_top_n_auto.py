import requests


def get_top_n_cryptos_cmc(n=100):

    CMC_API_KEY = "26e05643-82c5-411a-86ea-682cd0c6fc50"
    url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
    headers = {"Accept": "application/json", "X-CMC_PRO_API_KEY": CMC_API_KEY}
    params = {"start": "1", "limit": n + 20, "convert": "USD"}
    response = requests.get(url, headers=headers, params=params)
    if response.status_code != 200:
        print(f"[ERROR] CMC returned {response.status_code}")
        return []

    data = response.json().get("data", [])

    stablecoins = {
        "usdt",
        "usdc",
        "busd",
        "dai",
        "tusd",
        "usdp",
        "usdd",
        "gusd",
        "ustc",
        "frax",
        "lusd",
        "mim",
        "usn",
        "xaut",
        "ageur",
        "pyusd",
        "fdusd",
        "usd1"
    }
    
    result_symbols = []
    for item in data:
        if item["symbol"].lower() in stablecoins:
            continue

        yahoo_symbol = item["symbol"] + "-USD"
        result_symbols.append(yahoo_symbol)
        if len(result_symbols) >= n:
            break
    return result_symbols


if __name__ == "__main__":
    top_coins = get_top_n_cryptos_cmc(n=30)
    for c in top_coins:
        print(c)
