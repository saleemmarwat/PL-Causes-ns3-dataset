# PL-CausesNS3-50k

**ML-FGA: A Machine Learning Framework for Fine-Grained Analysis of Packet Loss Causes in MANETs and IoT Networks**

[![IEEE DataPort](https://img.shields.io/badge/Dataset-IEEE%20DataPort-blue)](https://ieee-dataport.org/documents/pl-causesns3-50k)
[![DOI](https://img.shields.io/badge/DOI-10.21227%2F86dj--xd45-orange)](https://doi.org/10.21227/86dj-xd45)

---

## Overview

This repository contains the complete reproducibility package for the ML-FGA paper, including:

- NS-3.45 simulation script
- Feature aggregation pipeline
- Temporal label assignment calendar
- Trained model evaluation notebook
- Final labeled dataset (50,000 records)

The dataset spans **five packet loss cause categories**: benign, congestion, mobility, interference, and malicious, generated from **84 independent NS-3.45 simulation runs** across diverse MANET and IoT protocol configurations.

---

## Repository Structure

```
PL-Causes-ns3-dataset/
│
├── plc_scenario.cc                         # NS-3.45 simulation script
├── run_50k.sh                              # Shell script to run all 84 simulation runs
├── aggregate_ns3_like.py                   # Feature aggregation pipeline (raw logs → 35 features)
├── aggregate_full.py                       # Full aggregation with all configurations
├── calendar_600_5regimes.json              # Temporal regime calendar for label assignment
├── ns3_like_packet_loss_causes_v1_50k.csv  # Final labeled dataset (50,000 records)
├── packet_loss_classification_after_review.ipynb  # Training and evaluation notebook
└── README.md
```

---

## Requirements

### NS-3 Simulation
```
NS-3.45 (https://www.nsnam.org/releases/ns-3-45/)
  Modules required: core, network, mobility, internet,
                    wifi, applications, traffic-control
C++ compiler: g++ >= 9.0
```

### Python Pipeline
```
Python >= 3.8
pandas >= 1.3
numpy >= 1.21
scikit-learn >= 1.0
xgboost >= 1.6
imbalanced-learn >= 0.9
shap >= 0.41
matplotlib >= 3.4
```

Install all Python dependencies:
```bash
pip install pandas numpy scikit-learn xgboost imbalanced-learn shap matplotlib
```

---

## Step-by-Step Reproduction

### Step 1 — Install NS-3.45

```bash
wget https://www.nsnam.org/releases/ns-allinone-3.45.tar.bz2
tar -xjf ns-allinone-3.45.tar.bz2
cd ns-allinone-3.45
python3 build.py --enable-examples --enable-tests
```

### Step 2 — Copy Simulation Script

```bash
cp plc_scenario.cc \
   ns-allinone-3.45/ns-3.45/scratch/plc_scenario.cc

cd ns-allinone-3.45/ns-3.45
./ns3 build 2>&1 | grep -E "error:|Finished"
```

### Step 3 — Run All 84 Simulation Runs

```bash
chmod +x run_50k.sh
./run_50k.sh
```

This script runs 84 NS-3 simulation runs across all protocol/mobility/PHY combinations listed in Table 2 of the paper. Each run produces four output files:

```
ns3-output/run_N_tx.csv      # Packet transmission events
ns3-output/run_N_rx.csv      # Packet reception events with delay
ns3-output/run_N_radio.csv   # Per-packet SNR and signal measurements
ns3-output/run_N_queue.csv   # Queue length and drop counters
```

Expected runtime: approximately 2-4 hours for all 84 runs on a standard desktop.

### Step 4 — Aggregate Features

```bash
python3 aggregate_ns3_like.py \
    --input_dir ns3-output/ \
    --calendar calendar_600_5regimes.json \
    --output ns3_like_packet_loss_causes_v1_50k.csv
```

This script:
- Reads raw NS-3 logs for each run
- Computes 35 per-window features in 1-second windows
- Assigns ground-truth labels using the temporal regime calendar
- Outputs the final 50,000-record labeled dataset

### Step 5 — Train and Evaluate Models

Open and run the Jupyter notebook:

```bash
jupyter notebook packet_loss_classification_after_review.ipynb
```

The notebook reproduces all results from the paper including:
- XGBoost, Random Forest, SVM, MLP training
- Per-class performance analysis (Table 9)
- McNemar's test results
- 5-fold cross-validation (accuracy 89.73% ± 0.17%)
- SMOTE and cost-sensitive experiments
- SHAP explainability analysis (10,000-sample)
- All figures from Section VI

---

## Dataset Description

| Property | Value |
|---|---|
| Total records | 50,000 |
| Features | 35 network-layer observables |
| Classes | 5 (benign, congestion, mobility, interference, malicious) |
| Train / Test split | 80% / 20% (stratified) |
| Simulation runs | 84 |
| Simulation duration | 600 seconds per run |
| Routing protocols | OLSR, AODV, DSR, RPL |
| Physical layers | IEEE 802.11n, 802.11g, 802.15.4 |
| Mobility models | RandomWaypoint, GaussMarkov, Static |

### Class Distribution

| Class | Records | % | Train | Test |
|---|---|---|---|---|
| benign | 24,859 | 49.7 | 19,885 | 4,974 |
| mobility | 7,557 | 15.1 | 5,990 | 1,567 |
| congestion | 7,411 | 14.8 | 6,016 | 1,395 |
| malicious | 5,102 | 10.2 | 4,035 | 1,067 |
| interference | 5,071 | 10.1 | 4,074 | 997 |

### Feature Groups

| Group | Features | FGA Connection |
|---|---|---|
| MAC Layer | PRR, mac_tx_attempts, mac_tx_success, mac_retries, backoff_slots, collisions | Extends P_M |
| Queue/Congestion | queue_len_norm, queue_drops, PQ, channel_busy_ratio, pps | Implements P_Q |
| Mobility | link_changes_per_s, speed_mps, neighbor_count, Peta | Implements P_η |
| Signal/PHY | rssi_dbm, snr_db, noise_floor_dbm, tx_power_dbm | New — not in FGA |
| Routing/QoS | ETX, delay_ms, jitter_ms, PM, ttl_hops | Extends FGA |
| Config/Topology | node_count, sim_area_m, routing, mobility_model, phy, transport | Generalization |

---

## Temporal Regime Calendar

Ground-truth labels are assigned using a temporal regime calendar that divides each 600-second simulation into five non-overlapping 120-second regimes:

| Regime | Window (s) | Label | Physical Scenario |
|---|---|---|---|
| 1 | 0–120 | benign | Normal operation |
| 2 | 120–240 | congestion | High data rate, queue overflow |
| 3 | 240–360 | mobility | High speed, frequent link breaks |
| 4 | 360–480 | interference | Low SNR, PHY errors, collisions |
| 5 | 480–600 | malicious | Blackhole, greyhole, selective forward |

The calendar configuration is stored in `calendar_600_5regimes.json`.

**Label Purity:** Loss Purity is 100% for all non-benign classes — every dropped packet carries a drop_reason matching the assigned regime label. See Section IV of the paper for full analysis.

---

## Key Results

| Model | Accuracy | F1-Macro | Malicious F1 | Train Time |
|---|---|---|---|---|
| **XGBoost** | **89.20%** | **85.41%** | **53.94%** | **71s** |
| MLP | 88.80% | 83.08% | 43.85% | 53.7s |
| SVM | 84.57% | 82.13% | 46.79% | 818.7s |
| Random Forest | 85.86% | 73.75% | 0.00% | 152.3s |

**5-fold cross-validation (XGBoost):** Accuracy 89.73% ± 0.17%, F1-Macro 85.83% ± 0.31%

---

## Citation

If you use this dataset or code, please cite:

```bibtex
@article{khan2026mlfga,
  title={ML-FGA: A Machine Learning Framework for Fine-Grained
         Analysis of Packet Loss Causes in MANETs and IoT Networks},
  author={Khan, Muhammad Saleem and Shahzad, Taimur and
          Sharif, Muhammad and Iqbal, Muhammad Ali and Kim, Soo Kyun},
  journal={IEEE Access},
  year={2026},
  doi={10.1109/ACCESS.2026.0000000}
}
```

Dataset citation:
```
M. S. Khan, "PL-CausesNS3-50k," IEEE DataPort, 2025.
DOI: 10.21227/86dj-xd45
https://ieee-dataport.org/documents/pl-causesns3-50k
```

---

## License

The dataset is released under the IEEE DataPort terms of use.
The code is released under the MIT License.

---

## Contact

Muhammad Saleem Khan
Department of Computer Science, COMSATS University Islamabad, Attock Campus
Email: saleem_khan@cuiatk.edu.pk
