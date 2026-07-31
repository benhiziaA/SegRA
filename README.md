# SegRA

SegRA is a deterministic research verifier for assessing whether a deployed
network segmentation architecture conforms to an intended architecture.

Given an intended architecture and its deployed realisation, SegRA identifies:

- exact zone mappings;
- valid segmentation refinements;
- missing or incorrectly placed assets;
- defense-in-depth deviations; and
- missing or inconsistent security controls.

SegRA produces a machine-readable JSON report and a detailed PDF report.

> SegRA is a research prototype and is not intended to replace a production
> network-security audit.

## Features

* Compares intended and deployed network zones
* Detects correctly and wrongly placed assets
* Evaluates defense-in-depth distance from external domains
* Classifies mapped zones as well located, overprotected, overexposed
* Compares required and implemented security controls
* Generates JSON and PDF evaluation reports
* Supports JSON inputs containing an `asp_facts` list
* Supports serialized realm JSON inputs


## Project Structure

```text
SegRA/
├── .github/
│   └── workflows/
│       └── tests.yml
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
│       └── .gitkeep
├── tests/
│   ├── data/
│   │   └── test_case/
│   │       ├── optimal_architecture.json
│   │       └── real_architecture.json
│   └── test_case.py
├── AUTHORS
├── CITATION.cff
├── LICENSE
├── NOTICE
├── README.md
├── codemeta.json
└── pyproject.toml
└── requirements.txt
```

## Requirements

* Python 3.11 or newer
* ReportLab 4.x

## Installation

Clone the repository and enter the project directory:

```bash
git clone https://github.com/benhiziaA/SegRA.git
cd SegRA
```

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

On Windows:

```bash
.venv\Scripts\activate
```

Install SegRA:

```bash
python -m pip install --upgrade pip
pip install -e .
```

To install the development and testing dependencies:

```bash
pip install -e ".[dev]"
```

## Input Files

By default, SegRA reads:

```text
examples/input/optimal_architecture.json
examples/input/real_architecture.json
```

Each input file should contain an `asp_facts` list.

The intended architecture used in the SegRA evaluation is generated using
Game of Zones (GoZ), an automated intent-based network micro-segmentation
methodology.

SegRA is a separate verification tool. It does not generate the intended
architecture itself; instead, it takes the GoZ-generated architecture as input
and assesses a deployed architecture against it.

The GoZ source code is not included or redistributed in this repository.

Related publication:

D. Canavese, R. Laborde, A. Laraba, A. Ferreira, and A. Benzekri,
“Game of Zones: An Automated Intent-Based Network Micro-segmentation
Methodology,” IEEE/IFIP NOMS 2025.

HAL: https://hal.science/hal-04948011v1

A minimal example is:

```json
{
  "type": "RealmLike",
  "stage": "example",
  "asp_facts": [
    "zone(normal, zone1).",
    "networkFunction(business, webServer).",
    "inZone(zone1, webServer)."
  ]
}
```

SegRA can also load supported serialized realm JSON files and extract the architecture facts required for evaluation.

## Usage

Run SegRA from the project root using the default example paths:

```bash
python -m segra.main
```
or

```bash
segra
```

This generates:

```text
examples/output/evaluation_report.json
examples/output/evaluation_report.pdf
```

Use custom architecture and report paths:

```bash
python -m segra.main \
  --optimal-json path/to/optimal_architecture.json \
  --real-json path/to/real_architecture.json \
  --report-json path/to/evaluation_report.json \
  --report-pdf path/to/evaluation_report.pdf
```

Alternatively, specify custom input and output directories:

```bash
python -m segra.main \
  --input-dir path/to/input \
  --output-dir path/to/output
```

## Evaluation Phases

### Phase 1: Zone Mapping and Asset Placement

SegRA maps each intended zone to one or more deployed zones and evaluates asset placement.

Assets are classified as:

* `good`: the asset is present in the expected mapped zone
* `wrong`: the asset is present in the mapped zone but does not belong there
* `miss`: the asset is expected but is absent from the mapped zone

The report also includes a summary of misplaced assets.

### Phase 2: Defense-in-Depth Evaluation

SegRA computes the depth of each zone as its distance from the nearest reachable external domain.

It compares the deployed depth with the intended depth and classifies each mapped zone as:

* `well_located`: the deployed depth matches the intended depth
* `over_protected`: the deployed zone is deeper than intended
* `overexposed`: the deployed zone is closer to an external domain than intended


### Phase 3: Security-Control Evaluation

SegRA compares the security controls required by the intended architecture with those present in the deployed architecture.

The report identifies:

* required controls that are available
* required controls that are missing
* implemented controls that are not required by the intended architecture

## Outputs

The JSON report contains structured results for all three evaluation phases. The PDF report contains readable tables covering:

* zone mappings and classifications
* missing and wrongly placed assets
* defense-in-depth distances and exposure classifications
* required, missing, and additional security controls

## Testing

Install the development dependencies:

```bash
pip install -e ".[dev]"
```

Run the complete test suite:

```bash
pytest -v
```

The tests validate the generated architecture case and verify:

* zone mapping and asset-placement results
* defense-in-depth classifications
* security-control findings
* JSON and PDF report generation

Tests are also executed automatically through GitHub Actions after pushes and pull requests.

## Reproducibility

The architecture files under `examples/input/` provide a complete executable evaluation example.

To reproduce the example:

```bash
python -m segra.main
```

To validate the generated test case automatically:

```bash
pytest -v
```

The same deterministic inputs produce the same evaluation classifications and structured JSON results.

## Limitations

SegRA is a research prototype.

* Its results depend on the correctness and completeness of the input architecture descriptions.
* It evaluates modeled architecture properties and does not inspect live network traffic.
* It supports the architecture facts and security-control representations documented in this repository.
* The implementation has not been hardened for untrusted or malicious input files.

## License

SegRA is released under the Apache License 2.0. See the `LICENSE` and `NOTICE` files for details.

## Archival

SegRA version `0.1.2` is preserved in the Software Heritage archive.

- Release: `v0.1.2`
- SWHID: [`swh:1:dir:ef729e9d63bbc50087fbd654ec40db660fe30751`](https://archive.softwareheritage.org/swh:1:dir:ef729e9d63bbc50087fbd654ec40db660fe30751;origin=https://github.com/benhiziaA/SegRA.git;visit=swh:1:snp:574d2770e64ca10112a6468b760eb6a447964f8e;anchor=swh:1:rev:a2e38a30f438c9162b662cdfbec35b79cac7258d)
- Source repository: https://github.com/benhiziaA/SegRA

## Research Status

SegRA is part of an ongoing scientific research project. A manuscript describing SegRA is being prepared for submission.

## Affiliations

This research was conducted at IRIT — Institut de Recherche en Informatique
de Toulouse, CNRS, Université de Toulouse.
