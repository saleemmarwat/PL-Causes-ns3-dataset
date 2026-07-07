import argparse, json
from pathlib import Path
import pandas as pd, numpy as np

def load_calendar(p):
    p = Path(p)
    if p.suffix.lower() == ".json":
        cal = json.loads(p.read_text())
        return [(w["start"], w["end"], w["label"]) for w in cal["windows"]]
    elif p.suffix.lower() == ".csv":
        df = pd.read_csv(p)
        return [(int(r.start), int(r.end), str(r.label)) for r in df.itertuples()]
    return []

def label_at(t, wins):
    for s, e, l in wins:
        if s <= t < e:
            return l
    return "benign"

def zscore(s):
    m = s.mean()
    sd = s.std(ddof=0)
    if sd == 0 or not np.isfinite(sd):
        return pd.Series(np.zeros(len(s)), index=s.index)
    return (s - m) / sd

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outDir", required=True)
    ap.add_argument("--calendar", required=True)
    ap.add_argument("--runId", type=int, required=True)
    ap.add_argument("--window", type=float, default=1.0)
    ap.add_argument("--qmax", type=float, default=100.0)  # queue limit "100p"
    ap.add_argument("--snr_step_for_1", type=float, default=5.0,
                    help="ΔSNR (dB) that maps to mobility proxy 1.0")
    args = ap.parse_args()

    base = Path(args.outDir) / f"run_{args.runId}"
    tx    = pd.read_csv(f"{base}_tx.csv")       # t_s,node,bytes
    rx    = pd.read_csv(f"{base}_rx.csv")       # t_s,node,peer,bytes,delay_ms
    radio = pd.read_csv(f"{base}_radio.csv")    # t_s,freq_mhz,signal_dbm,noise_dbm
    q     = pd.read_csv(f"{base}_queue.csv")    # t_s,dev,qlen,qdrops

    # Bin timestamps
    for df in (tx, rx, radio, q):
        df["t_bin"] = (df["t_s"] // args.window).astype(int)

    # --- TX
    agg_tx = tx.groupby("t_bin").agg(
        tx_pkts=("bytes", "count"),
        tx_bytes=("bytes", "sum"),
    ).reset_index()

    # --- RX (neighbor churn)
    agg_rx_counts = rx.groupby("t_bin").agg(
        rx_pkts=("bytes", "count"),
        rx_bytes=("bytes", "sum"),
        delay_ms_avg=("delay_ms", "mean"),
        jitter_ms=("delay_ms", lambda s: s.diff().abs().mean()),
    ).reset_index()

    peers = rx.groupby(["t_bin", "node"]).agg(
        peers=("peer", lambda s: set(s.values))
    ).reset_index().sort_values(["node", "t_bin"])
    peers["peers_prev"] = peers.groupby("node")["peers"].shift(1)

    def churn(row):
        A, B = row["peers"], row["peers_prev"]
        if not isinstance(A, set) or not isinstance(B, set) or len(A | B) == 0:
            return 0.0
        return len(A ^ B) / len(A | B)

    peers["neighbor_churn"] = peers.apply(churn, axis=1)
    peers["distinct_peers"] = peers["peers"].map(lambda s: len(s) if isinstance(s, set) else 0)
    agg_rx_churn = peers.groupby("t_bin").agg(
        neighbor_churn=("neighbor_churn", "mean"),
        distinct_peers=("distinct_peers", "mean"),
    ).reset_index()

    agg_rx = agg_rx_counts.merge(agg_rx_churn, on="t_bin", how="left").fillna(0.0)

    # --- Radio
    radio["snr_db"] = radio["signal_dbm"] - radio["noise_dbm"]
    agg_radio = radio.groupby("t_bin").agg(snr_db=("snr_db", "mean")).reset_index()

    # --- Queue (per-device max, then deltas)
    q_max = q.groupby(["t_bin", "dev"]).agg(
        qdrops=("qdrops", "max"),
        qlen_pkts=("qlen", "max"),
    ).reset_index().sort_values(["dev", "t_bin"])
    q_max["qdrops_prev"] = q_max.groupby("dev")["qdrops"].shift(1).fillna(0)
    q_max["qdrops_delta"] = (q_max["qdrops"] - q_max["qdrops_prev"]).clip(lower=0)

    agg_q = q_max.groupby("t_bin").agg(
        qdrops_delta=("qdrops_delta", "sum"),
        qlen_pkts_mean=("qlen_pkts", "mean"),
    ).reset_index()
    agg_q["queue_len_norm"] = (agg_q["qlen_pkts_mean"] / args.qmax).clip(0, 1)
    agg_q["queue_drops_rate"] = agg_q["qdrops_delta"] / 1.0  # per 1 s

    # --- Join
    bins = sorted(set(agg_tx.t_bin) | set(agg_rx.t_bin) | set(agg_radio.t_bin) | set(agg_q.t_bin))
    df = (
        pd.DataFrame({"t_bin": bins})
        .merge(agg_tx, on="t_bin", how="left")
        .merge(agg_rx, on="t_bin", how="left")
        .merge(agg_radio, on="t_bin", how="left")
        .merge(agg_q, on="t_bin", how="left")
        .fillna(0.0)
    )

    # KPIs
    df["PRR"] = (df["rx_pkts"] / df["tx_pkts"]).where(df["tx_pkts"] > 0, 0.0).clip(0, 1)
    df["ETX"] = (1.0 / df["PRR"]).where(df["PRR"] > 0, 100.0).clip(1, 100)
    df["pps"] = df["tx_pkts"] / args.window

    # Mobility = max(neighbor_churn, SNR volatility normalized)
    df["neighbor_churn"] = df.get("neighbor_churn", 0.0)
    snr = df["snr_db"].replace([np.inf, -np.inf], np.nan).ffill().bfill()
    df["snr_db"] = snr

    df["snr_volatility"] = snr.diff().abs().fillna(0.0)
    df["snr_vol_norm"] = (df["snr_volatility"] / max(1e-9, args.snr_step_for_1)).clip(0, 1)

    df["Peta"] = np.maximum(df["neighbor_churn"], df["snr_vol_norm"])
    df["link_changes_per_s"] = df["Peta"]  # keep column name for compatibility

    # Congestion/interference proxies
    df["PQ"] = np.where(df["tx_pkts"] > 0, (df["qdrops_delta"] / df["tx_pkts"]).clip(0, 1), 0.0)
    df["PM"] = 1.0 / (1.0 + np.exp((df["snr_db"] - 12.0) / 2.5))

    # Labels & loss flag
    wins = load_calendar(args.calendar)
    df["timestamp"] = df["t_bin"] * args.window
    df["cause_label"] = [label_at(float(t), wins) for t in df["timestamp"]]
    df["loss_occurred"] = (df["PRR"] < 0.999).astype(int)

   # --- QC evidence + benign-aware flag ---
	Emob  = 0.5 * zscore(df["Peta"]) \
	      + 0.3 * zscore(df.get("distinct_peers", pd.Series(0.0, index=df.index))) \
	      + 0.2 * zscore(df["pps"])
	Econg = 0.5 * zscore(df["PQ"]) \
	      + 0.3 * zscore(df["queue_len_norm"]) \
	      + 0.2 * zscore(df["ETX"])
	Eintf = 0.5 * zscore(-df["snr_db"]) \
	      + 0.3 * zscore(df["jitter_ms"].fillna(0)) \
	      + 0.2 * zscore(df["delay_ms_avg"].fillna(0))
	Emal  = np.zeros_like(Emob)  # reserved

	E = np.vstack([Emob, Econg, Eintf, Emal]).T
	idx2lbl = np.array(["mobility", "congestion", "interference", "malicious"])
	top = idx2lbl[E.argmax(axis=1)]

	tau_diff = 1.0   # as in paper
	tau_benign = 1.0 # require strong evidence to contradict "benign"

	qc = []
	for i, y in enumerate(df["cause_label"]):
	    if y == "benign":
		qc.append(1 if E[i, :3].max() >= tau_benign else 0)
	    else:
		j = {"mobility":0,"congestion":1,"interference":2,"malicious":3}[y]
		margin = E[i,:].max() - E[i,j]
		qc.append(1 if (idx2lbl[E[i,:].argmax()] != y and margin >= tau_diff) else 0)
	df["qc_flag"] = np.array(qc, dtype=int)

    cols = [
        "timestamp", "pps", "PRR", "ETX",
        "queue_len_norm", "queue_drops_rate",
        "link_changes_per_s", "snr_db", "PM", "PQ", "Peta",
        "loss_occurred", "cause_label", "qc_flag",
    ]
    out = Path(args.outDir) / f"window_run_{args.runId}.csv"
    df[cols].to_csv(out, index=False)
    print(f"[aggregate_full] wrote {out} ({len(df)} rows)")

if __name__ == "__main__":
    main()

