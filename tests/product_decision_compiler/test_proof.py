from ai_native_studio.product_decision_compiler.proof import run_alignment_proof


def test_alignment_proof_passes_end_to_end() -> None:
    report = run_alignment_proof()

    assert report.passed is True
    assert report.approval_status == "accepted"
    assert report.package.status == "approved"
    assert report.digest.review_items == 4
    assert any(case.id == "duplicate-check" and case.status == "rejected" for case in report.cases)
    assert any(
        case.id == "stale-version-check" and case.status == "rejected"
        for case in report.cases
    )
