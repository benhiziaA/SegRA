from pathlib import Path

from segra.comparator import evaluate_architecture


DATA_DIR = Path(__file__).parent / "data" / "test_case"


def test_architecture_case(tmp_path: Path):
    report_json = tmp_path / "evaluation_report.json"
    report_pdf = tmp_path / "evaluation_report.pdf"

    result = evaluate_architecture(
        optimal_json=str(DATA_DIR / "optimal_architecture.json"),
        real_json=str(DATA_DIR / "real_architecture.json"),
        report_json=str(report_json),
        report_pdf=str(report_pdf),
    )

    step1 = result["step1_before"]
    step2 = result["step2_before"]
    step3 = result["step3_before"]

    # Step1: mapping and asset-placement 
    assert step1["zone1"]["matched_zone"] == "zone2"
    assert step1["zone1"]["type"] == "violation"
    assert step1["zone1"]["good"] == [
        "automaticLogRetrievalService",
        "billingManagementService",
    ]
    assert step1["zone1"]["miss"] == ["logsServer"]

    assert step1["zone2"]["matched_zone"] == "zone3"
    assert step1["zone2"]["type"] == "violation"

    assert step1["zone3"]["matched_zone"] == "zone1"
    assert step1["zone3"]["type"] == "violation"

    for zone in ["zone4", "zone5", "zone6", "zone7", "zone8", "zone9"]:
        assert step1[zone]["type"] == "miss"
        assert step1[zone]["matched_zone"] is None

    assert step1["_unmatched_real_zones"] == []

    # step2: defense-in-depth
    assert step2["zone1"]["d_opt"] == 0
    assert step2["zone1"]["d_real"] == 1
    assert step2["zone1"]["classification"] == "over_protected"

    assert step2["zone2"]["d_opt"] == 3
    assert step2["zone2"]["d_real"] == 0
    assert step2["zone2"]["classification"] == "overexposed"

    assert step2["zone3"]["d_opt"] == 4
    assert step2["zone3"]["d_real"] == 1
    assert step2["zone3"]["classification"] == "overexposed"

    for zone in ["zone4", "zone5", "zone6", "zone7", "zone8", "zone9"]:
        assert step2[zone]["classification"] == "no_mapping_available"

    # step3: security-control 
    assert step3["zone1"]["compliant"] is False
    assert step3["zone1"]["missing_controls"] == [
        "ALF(billingManagementService)",
        "ALF(logsServer)",
    ]

    assert step3["zone2"]["compliant"] is False
    assert step3["zone2"]["missing_controls"] == [
        "ALF(brakeControlService)",
        "ALF(engineControlService)",
        "ALF(steeringControlService)",
        "PF",
    ]

    assert step3["zone3"]["compliant"] is False
    assert step3["zone3"]["missing_controls"] == [
        "ALF(doorControlOverrideService)",
        "ALF(steeringSelectionService)",
        "PF",
    ]

    # Reports were generated successfully
    assert report_json.exists()
    assert report_json.stat().st_size > 0
    assert report_pdf.exists()
    assert report_pdf.stat().st_size > 0