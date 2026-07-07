#!/usr/bin/env bash
set -euo pipefail

NS3="$HOME/ns3/ns-3-allinone/ns-3.45"
OUT="$HOME/ns3-output"
CAL="$HOME/calendar_600_5regimes.json"

mkdir -p "$OUT"

# 1) Simulate (adjust -P to your CPU cores)
seq 1 84 | xargs -I{} -P 4 bash -c '
  SEED="{}"
  cd "$NS3"
  ./ns3 run "scratch/plc_scenario --outDir=$OUT --calendar=$CAL --seed=$SEED --runId=$SEED --simSeconds=600"
'

# 2) Aggregate to the wide schema with malicious support (all runs)
seq 1 84 | xargs -I{} -P 4 python3 "$(pwd)/aggregate_ns3_like.py" --outDir "$OUT" --calendar "$CAL" --runId {}

# 3) Concatenate and trim to 50k rows
python3 - <<'PY'
import pandas as pd, glob, os
out=os.path.expanduser("~/ns3-output")
dfs=[pd.read_csv(p) for p in sorted(glob.glob(f"{out}/ns3_like_run_*.csv"))]
df=pd.concat(dfs, ignore_index=True)
df.to_csv(f"{out}/ns3_like_all.csv", index=False)
df.iloc[:50000].to_csv(f"{out}/ns3_like_50k.csv", index=False)
print("Wrote:", f"{out}/ns3_like_50k.csv", "rows:", len(pd.read_csv(f"{out}/ns3_like_50k.csv")))
PY
