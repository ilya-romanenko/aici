# Landing Copy Guidelines

## Header
- Tagline: "AI Crypto Index - transparent AI analytics with a developer-first API."
- Navigation: "Platform", "Metrics", "API", "Contact".
- CTA: "Get API key" with the caption "Request access to the data feed and demo console."
- Support banner: "Methodology documentation ->".

## Hero Block
- Eyebrow: "Professional AI analytics for the crypto market".
- Headline: "Assemble a managed crypto asset index in minutes".
- Subheadline: "Machine learning models aggregate markets, calculate weights, and surface the risk profile in a single dashboard."
- CTA primary: "Get demo access" with the caption "Open the dashboard and API endpoints".
- CTA secondary: "View performance" with the caption "Review CAGR, volatility, and the equity curve".
- Proof points under CTA: "Regular data updates | Verified sources | CSV and API exports".

## Trust Elements
- "Verified data": "End-to-end quote validation, quality filters, and a public changelog".
- "Transparent methodology": "Open calculation pipeline, documentation for models, and backtests".
- "Automated risk control": "Covariance matrices, stress tests, and alerts for volatility spikes".

## API Block
- Headline: "API for developers and quant teams".
- Subheadline: "REST and WebSocket endpoints with historical and live index metrics".
- Description: "Connect indexation and risk metrics to your products without heavy integration work. Documentation and SDKs unlock immediately after the key is activated".
- Feature cards:
  - "Historical data": "5+ years of quotes, asset weights, and KPIs in a single response".
  - "Alerts": "Webhooks for risk events and index rebalances with no delay".
  - "Security": "IP allow lists, key rotation, and usage metrics inside the console".
- Code snippet: "GET /v1/index/weights?date=latest -> returns weights plus Sharpe, Sortino, and Max Drawdown".
- CTA: "Request API access" with the caption "Generate a key and explore the demo endpoints".
- Performance note: "Latency benchmarks (~120 ms SLA) and rate limits are documented".

## Pricing
- Headline: "Pick the plan that fits your strategy".
- Subheadline: "Test for free and scale to institutional workloads".
- Free plan: "Up to 1,000 tokens per month (status checks are free; data reads cost 5 tokens; pipeline triggers are parameterized), T+1 data latency, demo dashboard access, email support within 48 hours".
- Pro plan: "Up to 100,000 tokens per month (pipeline triggers are parameterized), refresh every 15 minutes, CSV/JSON exports, prioritized support, and custom alerts".
- Enterprise plan: "Unlimited tokens, dedicated endpoint, SSO and SLA, white-label reports, and a technical account manager".
- Plan badges: "Free - 14 day trial", "Pro - most popular", "Enterprise - contact us".
- CTA under the table: "Start a subscription" with the caption "Choose a plan and schedule a demo call".
- Billing note: "Pipeline triggers charge base + parameter add-ons; status checks are free; data reads cost 5 tokens per call. Billing in USD with card, invoice, or USDC/USDT settlement".

## Audiences
- Headline: "Who benefits from AI Crypto Index".
- Subheadline: "Make it clear which problem the product solves for each role".
- Card 1 - "Crypto exchanges": "Improve listings and treasury management with ready-made indices and risk signals".
- Card 2 - "Funds and asset managers": "Use predictive weights and analytics for investment committees".
- Card 3 - "Algo traders and quant teams": "Plug the real-time API into strategies for rebalancing and stress tests".
- Card 4 - "Fintech and neobanks": "Embed the crypto index into your app with white-label reports".
- Card 5 - "Analysts and researchers": "Access historical data and model outputs for your own studies".

## Roadmap
- Headline: "Platform roadmap".
- Subheadline: "Maintain trust by showing the next releases and focus areas".
- Q1 2024: "Launch API v2, add liquidity metadata, improve SDK documentation".
- Q2 2024: "Add DeFi assets, integrate with Bloomberg Terminal, extend the stress-testing module".
- Q3 2024: "Custom indices with a strategy builder, multi-currency billing, ISO 27001 audit".
- Q4 2024: "Partner strategy marketplace, automated regulator reports, expanded 24/7 support team".
- Disclaimer: "Plans may adjust based on customer feedback. Latest updates live in the project Telegram channel".

## FAQ
- Question 1: "What data is included in the index and how often is it refreshed?" Answer: "We aggregate more than 50 liquid crypto assets, updating quotes every 15 minutes for Pro and Enterprise plans or daily for Free. Index weights adjust automatically whenever risk parameters shift".
- Question 2: "Can I access historical data for my own backtests?" Answer: "Yes. The /v1/index/history endpoint returns quotes, weights, and KPIs back to 2018. Downloads are available as CSV or JSON, and via S3".
- Question 3: "How do you meet compliance requirements?" Answer: "We undergo annual audits, store keys in HSM, maintain SOC 2 Type II controls, and support IP allow lists for Enterprise clients".
- Question 4: "Do you provide SDKs or integration examples?" Answer: "Python and TypeScript SDKs plus ready-to-run notebooks are available in the repository".
- Question 5: "How are risk metrics calculated and can I configure them?" Answer: "Sharpe, Sortino, Max Drawdown, and CVaR run on rolling windows. Enterprise tenants can adjust horizons and upload custom constraints".
- Question 6: "What happens if I exceed my API limits?" Answer: "We send webhooks and email notifications when you approach token quotas, then apply a soft throttle that slows token consumption. You can expand token limits in the console or through your account manager".
- Question 7: "Do you integrate with trading platforms and OMS?" Answer: "Yes. Connectors for TradingView, MetaTrader 5, and FIX aggregators are ready. We also share specs and a sandbox for custom OMS".
- Question 8: "Are there legal restrictions on using the index?" Answer: "The index is provided for research purposes and is not investment advice. US-based usage requires accredited investor status".
