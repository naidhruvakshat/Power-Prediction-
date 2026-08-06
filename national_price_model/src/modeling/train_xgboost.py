"""
train_xgboost.py

Trains one XGBoost regressor PER market_type (DAM, RTM, GDAM) to predict
mcp_rs_mwh, using the chronological train/validation/test splits from
split_dataset.py.

WHY SEPARATE MODELS PER MARKET, NOT ONE COMBINED MODEL:
  DAM, RTM, and GDAM clear at different times with different dynamics and
  even different data availability (DAM is missing all of 2023; RTM/GDAM
  aren't). This matches the project's own "market_type grouping" rule from
  the spec doc -- these markets' rows should never share lags or training
  signal with each other. Separate models keep that boundary clean.

WHY THESE COLUMNS ARE EXCLUDED FROM THE FEATURES:
  - purchase_bid_mw, sell_bid_mw, mcv_mw, final_scheduled_volume_mw: these
    are outputs of the SAME market-clearing auction as mcp_rs_mwh, cleared
    simultaneously. Using them to predict that block's own price is
    leakage -- in a real forecast you wouldn't have this block's bid/MCV
    data before the price is set, because they're determined together.
  - is_at_price_cap: computed directly from mcp_rs_mwh (the target) during
    feature engineering. Including it would let the model see a disguised
    version of the answer.
  - price_source_file: bookkeeping metadata, not a signal.
  - timestamp, market_type: timestamp's useful information is already
    captured via the cyclical encodings; market_type is the split key, not
    a feature (each market gets its own model).

NOWCASTING CAVEAT (documented, not hidden):
  demand_mw / wind_mw / solar_mw / net_load_mw are used as features for
  THIS block, i.e. the model assumes the actual realized grid state for
  the block being priced is already known. That's realistic for RTM
  (decided very close to real-time delivery) but optimistic for DAM
  (bid a day ahead, when only a forecast of net load would really be
  available, not the actual outturn). This is a reasonable first-pass
  modeling choice, not a deployment-ready assumption -- flagging it so
  it's a deliberate, known simplification rather than an accidental one.

MISSING VALUES: left as NaN (weather is only available 2024-04-01 onward;
lags are blank right after data gaps) -- XGBoost handles NaN splits
natively, so nothing is imputed or fabricated.

USAGE:
  python train_xgboost.py --splits-dir splits/ --out-dir models/
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import mean_absolute_error, mean_squared_error

TARGET = "mcp_rs_mwh"

EXCLUDE_COLS = {
    "timestamp", "market_type", "mcp_rs_mwh",
    "purchase_bid_mw", "sell_bid_mw", "mcv_mw", "final_scheduled_volume_mw",
    "is_at_price_cap", "price_source_file",
}


def load_split(path: Path, market: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df = df[df["market_type"] == market].copy()
    return df


def mape(y_true, y_pred) -> float:
    mask = y_true != 0
    return float(np.mean(np.abs((y_true[mask] - y_pred[mask]) / y_true[mask])) * 100)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--splits-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    results = {}
    for market in ["DAM", "RTM", "GDAM"]:
        print(f"\n{'='*60}\n{market}\n{'='*60}")

        train = load_split(args.splits_dir / "train.csv", market)
        val = load_split(args.splits_dir / "validation.csv", market)
        test = load_split(args.splits_dir / "test.csv", market)
        print(f"train: {len(train)}, validation: {len(val)}, test: {len(test)}")

        feature_cols = [c for c in train.columns if c not in EXCLUDE_COLS]

        X_train, y_train = train[feature_cols], train[TARGET]
        X_val, y_val = val[feature_cols], val[TARGET]
        X_test, y_test = test[feature_cols], test[TARGET]

        model = xgb.XGBRegressor(
            n_estimators=500,
            max_depth=6,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            reg_lambda=1.0,
            early_stopping_rounds=30,
            eval_metric="mae",
            random_state=42,
        )
        model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)
        print(f"Best iteration: {model.best_iteration}")

        market_result = {}
        for split_name, X, y in [("validation", X_val, y_val), ("test", X_test, y_test)]:
            pred = model.predict(X)
            mae = mean_absolute_error(y, pred)
            rmse = mean_squared_error(y, pred) ** 0.5
            mp = mape(y.to_numpy(), pred)
            market_result[split_name] = {"mae": round(mae, 2), "rmse": round(rmse, 2), "mape_pct": round(mp, 2), "n": len(y)}
            print(f"  {split_name}: MAE={mae:.2f} Rs/MWh, RMSE={rmse:.2f} Rs/MWh, MAPE={mp:.2f}%")

        importances = sorted(zip(feature_cols, model.feature_importances_), key=lambda x: -x[1])
        print("  Top 10 features by importance:")
        for name, imp in importances[:10]:
            print(f"    {name}: {imp:.4f}")

        model_path = args.out_dir / f"xgboost_{market.lower()}.json"
        model.save_model(model_path)
        results[market] = {
            "metrics": market_result,
            "feature_importance": [(n, float(i)) for n, i in importances],
            "n_features": len(feature_cols),
            "best_iteration": int(model.best_iteration),
        }
        print(f"  Saved model -> {model_path}")

    with open(args.out_dir / "results_summary.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSummary written -> {args.out_dir / 'results_summary.json'}")


if __name__ == "__main__":
    main()
