# PL-Causes-ns3-dataset
This repository includes the dataset for Packet loss causes in IoT networks
# Create and enter a docs folder
mkdir -p PLCauses-NS3-docs && cd PLCauses-NS3-docs

# README.md
cat > README.md <<'EOF'
# PLCauses-NS3: Packet-Loss Causation Dataset (ns-3)

**Version:** 1.0  
**Release date:** 2025-09-08  
**Format:** CSV / Parquet (window-level), CSV (raw logs)

PLCauses-NS3 is a reproducible dataset for **fine-grained packet-loss causation** in MANET/IoT-like wireless networks, generated with **ns-3 (v3.45)**. A time **calendar** activates distinct operating regimes—**benign**, **congestion**, **mobility**, **interference**—yielding labeled 1-second windows with networking/ML features:

- KPIs: `PRR`, `ETX`, `snr_db`, `pps`  
- Queueing: `queue_len_norm`, `queue_drops_rate`  
- Mobility/interference proxies: `Peta` (mobility), `PM` (PHY/MAC), `PQ` (queue pressure)  
- Topology dynamics: `link_changes_per_s` (neighbor churn, 0..1)  
- Labels & QA: `cause_label`, `loss_occurred`, `qc_flag`

The release includes **raw logs** (TX/RX/PHY/queue) and **window-level tables**, plus scripts to **regenerate** from a pinned ns-3 commit.


---

## 2) Field overview (see `docs/SCHEMA.json` for details)

**Window-level columns (1 s bins):**  
- `timestamp` (s): window start (float)  
- `pps` (pkts/s): offered load (TX packets per window)  
- `PRR` (0..1): packet reception ratio  
- `ETX` (≥1): 1/PRR, capped at 100  
- `queue_len_norm` (0..1): mean queue length / queue cap (`qmax`)  
- `queue_drops_rate` (pkts/s): queue drops per window  
- `link_changes_per_s` (0..1): **neighbor churn** (Jaccard delta vs previous window, averaged over nodes)  
- `snr_db` (dB): mean signal-noise ratio at PHY monitor  
- `PM` (0..1): PHY/MAC loss proxy from SNR (logistic around 12 dB)  
- `PQ` (0..1): queue pressure (drops/tx)  
- `Peta` (0..1): mobility proxy (from neighbor churn)  
- `loss_occurred` {0,1}: PRR < 0.999  
- `cause_label` ∈ {benign, congestion, mobility, interference} (malicious reserved for future)  
- `qc_flag` {0,1}: evidence disagrees with label by large margin

**Raw logs:** documented in `SCHEMA.json` and `INSTRUCTIONS.md`.

---

