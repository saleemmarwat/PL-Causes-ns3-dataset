#!/usr/bin/env python3
import argparse, json, os
from pathlib import Path
import pandas as pd, numpy as np
from datetime import datetime, timedelta

# ----------------------------- helpers --------------------------------
def load_calendar(p: str):
    """Return (windows, seed) from JSON or CSV calendar."""
    pth = Path(p)
    if pth.suffix.lower() == ".json":
        cal = json.loads(pth.read_text())
        wins = [(w["start"], w["end"], w["label"]) for w in cal["windows"]]
        seed = int(cal.get("seed", 0))
        return wins, seed
    elif pth.suffix.lower() == ".csv":
        df = pd.read_csv(pth)
        return [(int(r.start), int(r.end), str(r.label)) for r in df.itertuples()], 0
    return [], 0

def label_at(t, wins):
    for s, e, l in wins:
        if s <= t < e:
            return l
    return "benign"

def zscore(s: pd.Series):
    m = s.mean()
    sd = s.std(ddof=0)
    if sd == 0 or not np.isfinite(sd):
        return pd.Series(np.zeros(len(s)), index=s.index)
    return (s - m) / sd

def dominant_drop_reason(row):
    """
    Heuristic dominant drop reason for a 1 s window.
    Replace with true trace-derived reasons if you log MAC/PHY drops.
    """
    if row["cause_label"] == "congestion" and row["queue_drops_rate"] > 0.05:
        return "queue_overflow"
    if row["cause_label"] == "mobility" and row["Peta"] >= 0.4 and row["PRR"] < 0.9:
        return "link_break"
    if row["cause_label"] == "interference" and row["snr_db"] < 10 and row["PRR"] < 0.9:
        return "collision_exceeded"
    if row["cause_label"] == "malicious":
        if row["PRR"] < 0.7:
            return np.random.choice(
                ["blackhole_drop", "greyhole_drop", "selective_forward_block"]
            )
        else:
            return "none"
    return "none"

# ----------------------------- main -----------------------------------
def main():
    ap = argparse.ArgumentParser(description="Aggregate ns-3 raw logs to a wide, ns3-like schema with 'malicious' support.")
    ap.add_argument("--outDir", required=True, help="Folder with run_<id>_{tx,rx,radio,queue}.csv")
    ap.add_argument("--calendar", required=True, help="Calendar JSON/CSV with windows (can include 'malicious')")
    ap.add_argument("--runId", type=int, required=True)
    ap.add_argument("--window", type=float, default=1.0)
    ap.add_argument("--qmax", type=float, default=100.0)

    # Optional metadata to fill wide schema
    ap.add_argument("--simLabel", default="ns3_like_synth_v1")
    ap.add_argument("--simArea", default="500x500")
    ap.add_argument("--nodeCount", type=int, default=60)
    ap.add_argument("--simDuration", type=int, default=600)
    ap.add_argument("--mobilityModel", default="RandomWaypoint")
    ap.add_argument("--channelHelper", default="YansWifiChannel")
    ap.add_argument("--stack", default="WiFi")     # or "6LoWPAN"
    ap.add_argument("--routing", default="OLSR")
    ap.add_argument("--phy", default="802.11n")    # or 802.11g / 802.15.4
    ap.add_argument("--txPowerDbm", type=float, default=15.0)

    args = ap.parse_args()
    base = Path(args.outDir) / f"run_{args.runId}"

    # ---------- load raw logs ----------
    need = {
        "tx": f"{base}_tx.csv",
        "rx": f"{base}_rx.csv",
        "radio": f"{base}_radio.csv",
        "queue": f"{base}_queue.csv",
    }
    for k, p in need.items():
        if not Path(p).exists():
            raise FileNotFoundError(f"missing file: {p}")

    tx = pd.read_csv(need["tx"])       # t_s,node,bytes
    rx = pd.read_csv(need["rx"])       # t_s,node,peer,bytes,delay_ms
    radio = pd.read_csv(need["radio"]) # t_s,freq_mhz,signal_dbm,noise_dbm
    q = pd.read_csv(need["queue"])     # t_s,dev,qlen,qdrops

    for df in (tx, rx, radio, q):
        df["t_bin"] = (df["t_s"] // args.window).astype(int)

    # ---------- TX aggregates ----------
    # If you logged true packet sizes, feel free to replace this with real size column
    tx["packet_size_bytes"] = tx["bytes"].clip(lower=64, upper=1500)
    agg_tx = tx.groupby("t_bin").agg(
        tx_pkts=("bytes", "count"),
        tx_bytes=("bytes", "sum"),
        packet_size_bytes=("packet_size_bytes", "median"),
    ).reset_index()

    # ---------- RX aggregates ----------
    # Jitter within t_bin (per-flow ordering isn’t tracked; we approximate)
    rx = rx.sort_values(["t_bin", "t_s", "node", "peer"])
    rx["peer_shift"] = (rx["peer"] != rx.groupby(["node", "t_bin"])["peer"].shift(1)).astype(int)
    agg_rx = rx.groupby("t_bin").agg(
        rx_pkts=("bytes", "count"),
        rx_bytes=("bytes", "sum"),
        delay_ms_avg=("delay_ms", "mean"),
        jitter_ms=("delay_ms", lambda s: s.diff().abs().mean()),
        link_changes=("peer_shift", "sum"),
        neighbor_count=("peer", lambda s: s.nunique()),
    ).reset_index()

    # ---------- Radio aggregates ----------
    radio["snr_db"] = radio["signal_dbm"] - radio["noise_dbm"]
    # Remove inf/nan robustly and forward/backward fill
    snr = radio["snr_db"].replace([np.inf, -np.inf], np.nan).ffill().bfill()
    radio["snr_db"] = snr
    agg_radio = radio.groupby("t_bin").agg(
        snr_db=("snr_db", "mean"),
        rssi_dbm=("signal_dbm", "mean"),
        noise_floor_dbm=("noise_dbm", "mean"),
        # Unless you logged channel busy time, leave as 0.0 (or compute from WifiPhyState)
        channel_busy_ratio=("t_s", lambda s: 0.0),
    ).reset_index()

    # ---------- Queue aggregates (deltas per device, then sum) ----------
    q_dev = q.groupby(["t_bin", "dev"]).agg(
        qdrops=("qdrops", "max"),
        qlen_pkts=("qlen", "max"),
    ).reset_index()
    q_dev = q_dev.sort_values(["dev", "t_bin"])
    q_dev["qdrops_prev"] = q_dev.groupby("dev")["qdrops"].shift(1).fillna(0)
    q_dev["qdrops_delta"] = (q_dev["qdrops"] - q_dev["qdrops_prev"]).clip(lower=0)

    agg_q = q_dev.groupby("t_bin").agg(
        queue_drops=("qdrops_delta", "sum"),
        qlen_pkts_mean=("qlen_pkts", "mean"),
    ).reset_index()
    agg_q["queue_len_norm"] = (agg_q["qlen_pkts_mean"] / args.qmax).clip(0, 1)
    agg_q["queue_drops_rate"] = agg_q["queue_drops"] / args.window

    # ---------- Merge all ----------
    bins = sorted(set(agg_tx.t_bin) | set(agg_rx.t_bin) | set(agg_radio.t_bin) | set(agg_q.t_bin))
    df = (
        pd.DataFrame({"t_bin": bins})
        .merge(agg_tx, on="t_bin", how="left")
        .merge(agg_rx, on="t_bin", how="left")
        .merge(agg_radio, on="t_bin", how="left")
        .merge(agg_q, on="t_bin", how="left")
        .fillna(0.0)
    )

    # ---------- Core KPIs ----------
    df["PRR"] = (df["rx_pkts"] / df["tx_pkts"]).where(df["tx_pkts"] > 0, 0.0).clip(0, 1)
    df["ETX"] = (1.0 / df["PRR"]).where(df["PRR"] > 0, 100.0).clip(1, 100)
    df["pps"] = df["tx_pkts"] / args.window
    df["link_changes_per_s"] = df["link_changes"] / args.window

    # ---------- Proxies in [0,1] ----------
    df["PQ"] = np.where(df["tx_pkts"] > 0, (df["queue_drops"] / df["tx_pkts"]).clip(0, 1), 0.0)
    df["PM"] = 1.0 / (1.0 + np.exp((df["snr_db"] - 12.0) / 2.5))
    df["Peta"] = (df["link_changes_per_s"] / 5.0).clip(0, 1)

    # ---------- Labels from calendar ----------
    wins, ns3_seed = load_calendar(args.calendar)
    df["timestamp_s"] = df["t_bin"] * args.window
    df["cause_label"] = [label_at(float(t), wins) for t in df["timestamp_s"]]
    df["loss_occurred"] = (df["PRR"] < 0.999).astype(int)

    # ---------- QC: benign-aware ----------
    Emob = 0.5 * zscore(df["Peta"]) + 0.3 * zscore(df["neighbor_count"]) + 0.2 * zscore(df["pps"])
    Econg = 0.5 * zscore(df["PQ"]) + 0.3 * zscore(df["queue_len_norm"]) + 0.2 * zscore(df["ETX"])
    Eintf = 0.5 * zscore(-df["snr_db"]) + 0.3 * zscore(df["jitter_ms"].fillna(0)) + 0.2 * zscore(df["delay_ms_avg"].fillna(0))
    # simple heuristic for 'malicious' evidence: mixed anomalies
    Emal = 0.4 * zscore(df["PQ"]) + 0.4 * zscore(df["Peta"]) + 0.4 * zscore(-df["snr_db"])

    E = np.vstack([Emob, Econg, Eintf, Emal]).T
    idx2lbl = np.array(["mobility", "congestion", "interference", "malicious"])
    tau_diff = 1.0
    tau_benign = 1.0

    qc = []
    for i, y in enumerate(df["cause_label"]):
        if y == "benign":
            qc.append(1 if E[i, :3].max() >= tau_benign else 0)
        else:
            j = {"mobility": 0, "congestion": 1, "interference": 2, "malicious": 3}[y]
            margin = E[i, :].max() - E[i, j]
            qc.append(1 if (idx2lbl[E[i, :].argmax()] != y and margin >= tau_diff) else 0)
    df["qc_flag"] = np.array(qc, dtype=int)

    # ---------- Heuristic drop reason ----------
    df["drop_reason"] = [dominant_drop_reason(r) for _, r in df.iterrows()]

    # ---------- Wide schema fill ----------
    # Time stamps like your sample (increase per window)
    base_ts = datetime.utcnow()
    df["timestamp"] = [(base_ts + timedelta(seconds=int(s))).strftime("%Y-%m-%d %H:%M:%S") for s in df["timestamp_s"]]

    df["sim_label"] = args.simLabel
    df["run_id"] = args.runId
    df["ns3_seed"] = ns3_seed
    df["sim_area_m"] = args.simArea
    df["node_count"] = args.nodeCount
    df["sim_duration_s"] = args.simDuration
    df["mobility_model"] = args.mobilityModel
    df["channel_helper"] = args.channelHelper
    df["stack"] = args.stack
    df["routing"] = args.routing
    df["phy"] = args.phy
    df["tx_power_dbm"] = args.txPowerDbm

    # Placeholders unless you log per-flow/app metadata
    df["src_id"] = -1
    df["dst_id"] = -1
    df["flow_id"] = -1
    df["transport"] = np.where(df["stack"] == "WiFi", "UDP", "TCP")
    df["app"] = "CBR"
    df["ttl_hops"] = 0

    # MAC-level counters (approximate from tx/rx unless you log true MAC stats)
    df["mac_tx_attempts"] = df["tx_pkts"]
    df["mac_tx_success"] = df["rx_pkts"]  # lower bound
    df["rx_success"] = df["rx_pkts"]
    df["collisions"] = 0
    df["mac_retries"] = np.maximum(df["mac_tx_attempts"] - df["mac_tx_success"], 0)
    df["backoff_slots"] = 0

    # If speed not logged, set to 0
    if "speed_mps" not in df.columns:
        df["speed_mps"] = 0.0

    # ---------- Column order to mirror your sample ----------
    cols = [
        "sim_label","run_id","ns3_seed","sim_area_m","node_count","sim_duration_s",
        "mobility_model","channel_helper","stack","routing",
        "timestamp","src_id","dst_id","flow_id","transport","app","ttl_hops",
        "packet_size_bytes","pps","phy","tx_power_dbm","noise_floor_dbm","rssi_dbm","snr_db",
        "neighbor_count","speed_mps","channel_busy_ratio","queue_len_norm","link_changes_per_s",
        "collisions","mac_retries","backoff_slots","PRR","ETX","delay_ms_avg","jitter_ms",
        "PM","PQ","Peta","mac_tx_attempts","mac_tx_success","rx_success","queue_drops",
        "cause_label","loss_occurred","drop_reason","qc_flag"
    ]

    out = Path(args.outDir) / f"ns3_like_run_{args.runId}.csv"
    df[cols].to_csv(out, index=False)
    print(f"[aggregate_ns3_like] wrote {out} ({len(df)} rows)")

if __name__ == "__main__":
    main()

