# Implementation Plan: Fix P0-P3 Algorithmic Issues

Fix 12 algorithmic/logic issues found in code review, prioritized P0→P3.

## Priority summary

| Priority | Issue | File(s) | Impact |
|----------|-------|---------|--------|
| P0 | Confidence Calibrator only reduces, never increases | `confidence_calibrator.py` | Production accuracy |
| P0 | `_handle_system_query()` is empty stub | `supervisor.py` | Wasted resources |
| P1 | DRY: 3 copies of support detection logic | `simple_agent.py`, `supervisor.py` | Maintainability |
| P1 | Dead code: `_normalize_final_confidence()` unused | `supervisor.py` | Code debt |
| P2 | Intent cache has no TTL eviction | `supervisor.py` | Stale cache |
| P2 | Redundant pattern checks (DB calls) | `supervisor.py` | Performance |
| P3 | `_retry_with_backoff` closure pattern | `n8n_connector.py` | Clean code |
| P3 | Tool planning keyword false positives | `reasoning_loop.py` | Edge cases |
| P3 | N+1 async_session creation | `supervisor.py` | Connection pool |
| P3 | EnsembleScorer weights sub-optimal | `bayesian_confidence.py` | Marginal |

---

## P0 Fixes

### P0.1: Confidence Calibrator - Enable bidirectional calibration

**File:** `src/core/confidence_calibrator.py`  
**Problem:** `calibrate()` = `raw * calibration_factor`, where factor ∈ [0.5, 1.0]. This means confidence can ONLY decrease, never increase — even for users with 90%+ historical accuracy.  
**Fix:** Change calibration_factor bounds to [0.5, 1.5] (centered around 1.0), then clamp final result to [0.0, 1.0].

### P0.2: Fix `_handle_system_query()` stub

**File:** `src/core/supervisor.py`  
**Problem:** Method just returns `{"result": "system query result", "confidence": 0.9}` — but the full pipeline (tool planning, retry, budget tracking) exists around it.  
**Fix:** Add proper implementation that queries n8n connector or returns error message so pipeline flows correctly.

---

## P1 Fixes

### P1.1: DRY support detection

**Files:** `src/agents/simple_agent.py`, `src/core/supervisor.py`  
**Problem:** 3 copies of `_looks_like_support_request()`, `_build_support_clarification()`, `_looks_generic_support_reply()`.  
**Fix:** Move shared logic into `src/core/support_utils.py` and import from both files.

### P1.2: Remove dead `_normalize_final_confidence()`

**File:** `src/core/supervisor.py`  
**Problem:** Method defined at line 1051 but never called anywhere.  
**Fix:** Remove the method.

---

## P2 Fixes

### P2.1: Intent cache with TTL eviction

**File:** `src/core/supervisor.py`  
**Problem:** Cache eviction removes oldest 25% of entries but has no TTL check.  
**Fix:** Add timestamp-based eviction: remove entries older than `_intent_cache_ttl` (300s) on access.

### P2.2: Eliminate redundant pattern checks

**File:** `src/core/supervisor.py`  
**Problem:** `process()` calls `_check_patterns()` in subagents path (line 581) and then again in non-subagents path (line 623).  
**Fix:** Move pattern check before the decision branch and reuse the result.

---

## P3 Fixes

### P3.1: Tool planning keyword safety

**File:** `src/core/reasoning_loop.py`  
**Problem:** `_plan_tool()` uses simple containment check that causes false positives.  
**Fix:** Add word-boundary matching and negative lookahead for separator words.

### P3.2: EnsembleScorer weight hygiene

**File:** `src/core/bayesian_confidence.py`  
**Problem:** Model quality weight (0.3) based solely on Beta mean.  
**Fix:** Add variance penalty — high-variance models should get lower weight.