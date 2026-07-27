# SegRA

SegRA is a Python tool for evaluating a real segmented network architecture against an optimal architecture. It compares asset placement, defense-in-depth structure, and security controls, then generates both a structured JSON report and a human-readable PDF report.

## Features

- Compares optimal and real network zones
- Detects correctly placed, misplaced, missing, and wrongly placed assets
- Evaluates defense-in-depth distance from external domains
- Classifies mapped zones as well located, overprotected, overexposed, or unmapped
- Compares required and implemented security controls
- Generates JSON and PDF evaluation reports
- Supports inputs that contain an `asp_facts` list
- Supports serialized realm JSON inputs

## Project Structure

```text
SegRA/
├── src/
│   └── segra/
│       ├── __init__.py
│       ├── comparator.py
│       └── main.py
├── examples/
│   ├── input/
│   │   ├── optimal_architecture.json
│   │   └── real_architecture.json
│   └── output/
│       ├── evaluation_report.json
│       └── evaluation_report.pdf
├── requirements.txt
├── LICENSE
├── NOTICE
└── README.md
```

## Requirements

- Python 3.9 or newer
- ReportLab

Install the required dependency:

```bash
python3 -m pip install -r requirements.txt
```

## Input Files

By default, SegRA reads the example input files:

```text
examples/input/optimal_architecture.json
examples/input/real_architecture.json
```

Each input file should contain an `asp_facts` list. A minimal example is shown below:

```json
{
  "type": "RealmLike",
  "stage": "example",
  "asp_facts": [
    "zone(zone1).",
    "inZone(zone1, webServer)."
  ]
}
```

SegRA can also load serialized realm JSON and extract the required architecture facts from it.

## Usage

Run SegRA from the project root with the default example paths:

```bash
python3 -m src.segra.main
```

This generates:

```text
examples/output/evaluation_report.json
examples/output/evaluation_report.pdf
```

The entrypoint can also be run directly:

```bash
python3 src/segra/main.py
```

Use custom files when evaluating another architecture:

```bash
python3 -m src.segra.main \
  --optimal-json path/to/optimal_architecture.json \
  --real-json path/to/real_architecture.json \
  --report-json path/to/evaluation_report.json \
  --report-pdf path/to/evaluation_report.pdf
```

Alternatively, use a custom input or output directory:

```bash
python3 -m src.segra.main \
  --input-dir path/to/input \
  --output-dir path/to/output
```

## Evaluation Phases

### Phase 1: Zone Mapping and Asset Placement

SegRA maps each optimal zone to one or more real zones and evaluates asset placement. Assets are classified as:

- `good`: the asset is present in the expected mapped zone
- `wrong`: the asset is present in the mapped zone but does not belong there
- `miss`: the asset is expected but missing from the mapped zone

The report also includes a summary of misplaced assets.

### Phase 2: Defense-in-Depth Evaluation

SegRA computes zone depth as the distance from the nearest reachable external domain. It compares the real depth against the optimal depth and classifies each mapped zone as:

- `well_located`: the real depth matches the optimal depth
- `over_protected`: the real zone is deeper than expected
- `overexposed`: the real zone is closer to an external domain than expected
- `no_mapping_available`: no real zone mapping is available

### Phase 3: Security Control Evaluation

SegRA compares the security controls required by the optimal architecture with the controls available in the real architecture. The PDF report summarizes controls as:

- required controls available
- missed controls
- unneeded controls

## Outputs

The JSON report contains structured evaluation data for all three phases:

```text
stage
evaluated_at
step1_before
step2_before
step3_before
```

The PDF report contains readable tables for:

- zone classification
- misplaced assets
- depth and exposure
- security controls

The PDF report does not include local file paths.

## License

This project is released under the license included in the `LICENSE` file.

## Notice

This software is part of an ongoing scientific research project. The associated research paper is currently under review.
