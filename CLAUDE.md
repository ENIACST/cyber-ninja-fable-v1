# Cyber Ninja v5.2 HYBRID — working rules

The governing doctrine for this repository is `docs/CYBER_NINJA_v5.2_HYBRID.md`. Read it before any change.

Non-negotiable, in short:

- EXECUTION = OFF. No code that places orders or holds exchange trading keys.
- FVG AUTHORITY = BLOCKED, PROMOTION = BLOCKED until the original Cyber Ninja FVG detector is integrated unchanged (§28, §82) and EEC replay reproduces historical Event IDs (§53).
- Never write, "improve" or approximate the FVG formula. Only the original detector source is accepted.
- History is append-only: no deleting or rewriting failed tests, baselines, ledgers or decisions. Corrections are new events.
- Every claim is labelled FACT / HYPOTHESIS / INFERENCE / UNKNOWN. MISSING ≠ FALSE.
- Primary data: Binance Futures BTCUSDT PERPETUAL. TradingView is not a market-data source.
- AI may propose; it may not approve its own changes, alter risk limits, or promote anything.
- Approved changes are appended under GOVERNANCE EVENTS at the end of the doctrine. CN-CP-001 (approved) fixes closed-bar law, candle boundaries, event time semantics, the EVENT_ID recipe and the DATASET_HASH recipe; code must follow it exactly.
- CN-CP-002 (approved) defines gate results (PASS/FAIL/MISSING/UNKNOWN, worst input wins, use `cyberninja/gates.py`), G5/G6 separation, archive closed-bar rule, data revisions, raw-OHLC basis and the Score vector.
- Proposals live in `docs/proposals/` and stay as written; only Authority's explicit approval moves one into the doctrine.
