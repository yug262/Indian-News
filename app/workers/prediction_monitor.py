"""
Prediction Monitor — Background worker that tracks AI predictions.

Runs every 3 minutes:
  1. Loads all non-finalized predictions
  2. Fetches current prices
  3. Updates MFE / MAE
  4. Finalizes when duration expires

Usage:
    python prediction_monitor.py
"""

import time
import traceback
import requests
from datetime import datetime, timezone, timedelta

from app.core.db import fetch_all, execute_query
from app.core.tools import _safe_last_close

# ── Config ──────────────────────────────────────────────
CHECK_INTERVAL_SECONDS = 30  # 3 minutes

# Reverse map: yfinance symbol → coingecko id  (for crypto fallback)
_YF_TO_COINGECKO = {
    "BTC-USD": "bitcoin",
    "ETH-USD": "ethereum",
    "SOL-USD": "solana",
    "XRP-USD": "ripple",
    "ADA-USD": "cardano",
    "DOGE-USD": "dogecoin",
    "AVAX-USD": "avalanche-2",
    "LINK-USD": "chainlink",
    "DOT-USD": "polkadot",
    "MATIC-USD": "matic-network",
    "SHIB-USD": "shiba-inu",
    "LTC-USD": "litecoin",
    "UNI-USD": "uniswap",
    "ATOM-USD": "cosmos",
}


_CG_SEARCH_CACHE = {}

def _get_coingecko_id(query: str) -> str | None:
    query = query.lower()
    if query in _CG_SEARCH_CACHE:
        return _CG_SEARCH_CACHE[query]
    
    try:
        r = requests.get(f"https://api.coingecko.com/api/v3/search?query={query}", timeout=10)
        data = r.json()
        if data.get("coins") and len(data["coins"]) > 0:
            cg_id = data["coins"][0]["id"]
            _CG_SEARCH_CACHE[query] = cg_id
            return cg_id
    except Exception:
        pass
    
    _CG_SEARCH_CACHE[query] = query # fallback
    return query

def _log(msg: str):
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode(), flush=True)


def _fetch_price(symbol: str) -> float | None:
    """
    Get the latest real-time price for a symbol.
    Uses ccxt for crypto assets and yfinance 1-minute intraday tick for indices/equities/forex.
    """
    import yfinance as yf
    from app.core.tools import _is_crypto, _crypto_to_binance, _safe_last_close
    
    query_symbol = symbol
    is_crypto_dynamic = False
    raw_name = symbol
    
    if symbol.startswith("CRYPTO:"):
        is_crypto_dynamic = True
        raw_name = symbol.replace("CRYPTO:", "")
        query_symbol = f"{raw_name.upper()}-USD"
    elif symbol.startswith("FOREX:"):
        raw_name = symbol.replace("FOREX:", "").replace("/", "").upper()
        query_symbol = f"{raw_name}=X"

    # 1) CCXT for Crypto
    if _is_crypto(query_symbol):
        try:
            import ccxt
            exchange = ccxt.binance()
            binance_sym = _crypto_to_binance(query_symbol)
            ticker = exchange.fetch_ticker(binance_sym)
            if ticker and ticker.get('last') is not None:
                return float(ticker['last'])
        except Exception as e:
            _log(f"CCXT fetch_ticker error for {query_symbol}: {e}")
            pass # Fall back to CoinGecko mapped via get_crypto_prices
        
        # CoinGecko fallback for crypto
        cg_id = _YF_TO_COINGECKO.get(symbol) or _YF_TO_COINGECKO.get(query_symbol)
        if is_crypto_dynamic and not cg_id:
            cg_id = _get_coingecko_id(raw_name)

        if cg_id:
            try:
                from app.core.tools import get_crypto_prices
                prices = get_crypto_prices([cg_id])
                if cg_id in prices and prices[cg_id] is not None:
                    return float(prices[cg_id])
            except Exception:
                pass
        return None

    # 2) yfinance for non-crypto
    try:
        # Use 5d period to ensure we catch the last traded price even over weekends/holidays
        tk = yf.Ticker(query_symbol)
        hist = tk.history(period="5d", interval="1m")
        if not hist.empty:
            valid_closes = hist["Close"].dropna()
            if not valid_closes.empty:
                return float(valid_closes.iloc[-1])
    except Exception:
        pass

    # Fallback to daily close (for weekends or off-hours)
    try:
        price = _safe_last_close(query_symbol)
        if price is not None:
            return price
    except Exception:
        pass

    return None


def _compute_move_pct(start_price: float, current_price: float) -> float:
    """Percentage move from start to current."""
    if start_price == 0:
        return 0.0
    return ((current_price - start_price) / start_price) * 100.0


def _finalize_prediction(pred: dict, final_price: float, now: datetime):
    """Determine final status and update DB."""
    # Retrieve prediction variables
    pred_id = pred["id"]
    start_price = float(pred["start_price"])
    direction = (pred["direction"] or "").strip()
    predicted_move = float(pred["predicted_move_pct"])
    mfe = float(pred["mfe_pct"] or 0)
    final_move = _compute_move_pct(start_price, final_price)

    # Small tolerance for floating-point rounding (0.005%)
    EPS = 0.005

    # Determine status
    if direction.lower() in ("positive", "bullish"):
        # Bullish target check — MFE stored as positive favorable move
        if mfe >= predicted_move - EPS:
            # It hit the target at least once
            if final_move > predicted_move + EPS:
                status = "overperformed"
            else:
                status = "hit"
        else:
            # It never hit the target
            if final_move > 0:
                status = "missed"
            else:
                status = "wrong"

    elif direction.lower() in ("negative", "bearish"):
        # Bearish target check — MFE stored as positive (abs favorable move)
        if mfe >= predicted_move - EPS:
            # It hit the target at least once (downwards)
            if abs(final_move) > predicted_move + EPS:
                status = "overperformed"
            else:
                status = "hit"
        else:
            # It never hit the target
            if final_move < 0:
                status = "missed"
            else:
                status = "wrong"
                
    elif direction.lower() == "neutral":
        if abs(final_move) <= 0.2:
            status = "hit"
        else:
            status = "wrong"
    else:
        status = "expired"

    execute_query(
        """UPDATE predictions SET
            finalized = TRUE,
            finalized_at = %s,
            final_price = %s,
            final_move_pct = %s,
            status = %s,
            last_checked_at = %s,
            last_price = %s,
            last_move_pct = %s
        WHERE id = %s""",
        (now, final_price, round(final_move, 4), status, now,
         final_price, round(final_move, 4), pred_id),
    )
    _log(f"  ✅ FINALIZED #{pred_id} {pred['asset']} → {status} "
         f"(final={final_move:+.2f}%, mfe={mfe:.2f}%)")


def check_predictions():
    """Main loop iteration: check and update all pending predictions."""
    preds = fetch_all(
        "SELECT * FROM predictions WHERE finalized = FALSE AND status = 'pending'"
    )

    if not preds:
        _log("[PRED] No pending predictions.")
        return

    _log(f"[PRED] Checking {len(preds)} pending prediction(s)...")
    now = datetime.now(timezone.utc)

    for pred in preds:
        pred_id = pred["id"]
        symbol = pred["asset"]
        news_id = pred["news_id"]
        
        try:
            # 1. Check if the parent news article still exists
            news_exists = fetch_all("SELECT id FROM news WHERE id = %s", (news_id,))
            if not news_exists:
                _log(f"  🗑️ #{pred_id} {symbol}: parent news #{news_id} deleted. Finalizing early.")
                execute_query(
                    "UPDATE predictions SET finalized = TRUE, status = 'expired', finalized_at = %s WHERE id = %s",
                    (now, pred_id)
                )
                continue

            # 2. Fetch the current price
            current_price = _fetch_price(symbol)
            if current_price is None:
                _log(f"  ⚠️ #{pred_id} {symbol}: price unavailable, skipping")
                continue

            start_price = float(pred["start_price"])
            direction = (pred["direction"] or "").strip().lower()
            predicted_move = float(pred["predicted_move_pct"])
            raw_move = _compute_move_pct(start_price, current_price)

            # MFE / MAE calculation depends on direction
            old_mfe = float(pred["mfe_pct"] or 0)
            old_mae = float(pred["mae_pct"] or 0)

            if direction in ("positive", "bullish"):
                favorable = raw_move   # positive move is favorable
                adverse = raw_move     # negative move is adverse
                new_mfe = max(old_mfe, favorable)
                new_mae = min(old_mae, adverse)
            elif direction in ("negative", "bearish"):
                favorable = -raw_move  # downward move is favorable (store as positive)
                adverse = -raw_move    # upward move is adverse
                new_mfe = max(old_mfe, favorable)
                new_mae = min(old_mae, adverse)
            else:
                # Neutral: MFE = max abs move, MAE same
                new_mfe = max(old_mfe, abs(raw_move))
                new_mae = min(old_mae, -abs(raw_move))

            # Check if duration expired
            start_time = pred["start_time"]
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=timezone.utc)
            duration_minutes = int(pred["expected_duration_minutes"])
            expiry_time = start_time + timedelta(minutes=duration_minutes)

            if now >= expiry_time:
                # Update MFE/MAE one last time before finalizing
                execute_query(
                    "UPDATE predictions SET mfe_pct = %s, mae_pct = %s WHERE id = %s",
                    (round(new_mfe, 4), round(new_mae, 4), pred_id),
                )
                pred["mfe_pct"] = new_mfe
                _finalize_prediction(pred, current_price, now)
            else:
                # Just update tracking fields
                execute_query(
                    """UPDATE predictions SET
                        last_checked_at = %s,
                        last_price = %s,
                        last_move_pct = %s,
                        mfe_pct = %s,
                        mae_pct = %s
                    WHERE id = %s""",
                    (now, current_price, round(raw_move, 4),
                     round(new_mfe, 4), round(new_mae, 4), pred_id),
                )
                remaining = expiry_time - now
                _log(f"  📊 #{pred_id} {symbol}: move={raw_move:+.2f}% "
                     f"mfe={new_mfe:.2f}% mae={new_mae:.2f}% "
                     f"(expires in {remaining})")

        except Exception as e:
            _log(f"  ❌ #{pred_id} {symbol}: ERROR {e}")
            traceback.print_exc()
            try:
                execute_query(
                    "UPDATE predictions SET status = 'error', error = %s WHERE id = %s",
                    (str(e)[:500], pred_id),
                )
            except Exception:
                pass