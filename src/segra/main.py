# Copyright 2026 SegRA contributors
# SPDX-License-Identifier: Apache-2.0
import argparse
from pathlib import Path

try:
    from .comparator import evaluate_architecture
except ImportError:
    from comparator import evaluate_architecture


PROJECT_DIR = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = PROJECT_DIR / "examples"
INPUT_DIR = EXAMPLES_DIR / "input"
OUTPUT_DIR = EXAMPLES_DIR / "output"

OPTIMAL_JSON = INPUT_DIR / "optimal_architecture.json"
REAL_JSON = INPUT_DIR / "real_architecture.json"
REPORT_JSON = OUTPUT_DIR / "evaluation_report.json"
REPORT_PDF = OUTPUT_DIR / "evaluation_report.pdf"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate a real architecture against an optimal architecture."
    )
    parser.add_argument("--input-dir", type=Path, default=INPUT_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--optimal-json", type=Path)
    parser.add_argument("--real-json", type=Path)
    parser.add_argument("--report-json", type=Path)
    parser.add_argument("--report-pdf", type=Path)
    args = parser.parse_args()

    input_dir = args.input_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    optimal_json = (args.optimal_json or input_dir / OPTIMAL_JSON.name).expanduser().resolve()
    real_json = (args.real_json or input_dir / REAL_JSON.name).expanduser().resolve()
    report_json = (args.report_json or output_dir / REPORT_JSON.name).expanduser().resolve()
    report_pdf = (args.report_pdf or output_dir / REPORT_PDF.name).expanduser().resolve()

    missing = [path for path in (optimal_json, real_json) if not path.exists()]
    if missing:
        formatted = "\n".join(f"- {path}" for path in missing)
        raise SystemExit(f"Missing input file(s):\n{formatted}")

    evaluate_architecture(
        optimal_json=str(optimal_json),
        real_json=str(real_json),
        report_json=str(report_json),
        report_pdf=str(report_pdf),
    )

    print("[SegRA] JSON report:", report_json)
    print("[SegRA] PDF report:", report_pdf)


if __name__ == "__main__":
    main()
