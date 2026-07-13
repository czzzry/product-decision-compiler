"""Run the complete offline Product Decision Compiler proof."""

from .proof import run_alignment_proof


def main() -> None:
    report = run_alignment_proof()
    print(report.render())
    if not report.passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
