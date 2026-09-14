# Vertex AI Research Report: Climatology & Grid Stress

**Thesis-Grade Grid Interconnection Risk Study**
Generated: 2026-09-14 00:51:17.001644+00:00

## 🔬 Empirical Methodology
To establish a robust prediction of **Winter Grid Stress Events (WGSE)**, we queried NOAA's GSOD database on BigQuery to assemble daily extreme values across 4 key regional weather stations (KAMA, KICT, KLNK, KOKC) spanning 2019 to 2024. These features were aligned with hourly SPP generation capacity records.

### Model settings & Hardware Acceleration
- **Ensemble**: Heterogeneous XGBoost and LightGBM ensemble.
- **Hardware**: GPU-accelerated training using the NVIDIA CUDA backend and `hist` tree building.
- **Split**: Chronological (60% training, 20% separate chronological sigmoid calibration, 20% untouched testing).

## 📊 Model Evaluation Results

| Model Name | Brier Score (lower is better) | Log Loss (lower is better) | ROC-AUC | Average Precision |
|---|---:|---:|---:|---:|
| XGBoost (GPU-Calibrated) | 0.000335 | 0.005325 | 1.000000 | 1.000000 |
| LightGBM (GPU-Calibrated) | 0.004200 | 0.016838 | 0.999581 | 0.992741 |
| **Ensemble (LGBM + XGBoost)** | **0.001363** | **0.008769** | **1.000000** | **1.000000** |

## 🔍 Feature Importances (Top Weather & Grid Variables)

- **KLNK_temp_f**: `111.3103`
- **KAMA_temp_f**: `85.0555`
- **KICT_temp_f**: `83.1041`
- **grid_temp_gradient**: `12.5045`
- **KOKC_temp_f**: `9.5059`
- **KLNK_wind_knots**: `9.0003`
- **KICT_wind_knots**: `7.0037`
- **KAMA_wind_knots**: `4.5003`
- **wind_ecomax_mw**: `0.0126`
- **month**: `0.0021`
- **KOKC_wind_knots**: `0.0006`
- **coal_market_ecomax_mw**: `0.0000`
- **natural_gas_ecomax_mw**: `0.0000`
- **solar_ecomax_mw**: `0.0000`
- **dispatchable_thermal_mw**: `0.0000`
- **hour**: `0.0000`
- **is_weekend**: `0.0000`