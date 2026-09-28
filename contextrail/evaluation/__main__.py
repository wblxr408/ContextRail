"""Command line for local smoke runs, real API runs and frozen SWE manifests."""

from __future__ import annotations

import argparse
from pathlib import Path

from .agent import MinimalApiAgent, run_stress_suite
from .dashboard import serve_dashboard
from .fixture_api import serve_fixture_api
from .models import DeterministicEvidenceModel, OpenAICompatibleModel
from .pricing import PriceBook
from .preflight import api_preflight
from .swebench import (environment_status, load_verified_subset, verifier_command,
                       write_official_task_inputs, write_subset_manifest)
from .tasks import ALL_TASKS, PRESSURE_TASKS, STRESS_TASKS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ContextRail API-agent evaluation host")
    commands = parser.add_subparsers(dest="command", required=True)
    stress = commands.add_parser("stress", help="Run the custom A/B/C context-strategy tasks")
    stress.add_argument("--model", choices=("fixture", "api"), default="fixture")
    stress.add_argument("--suite", choices=("control", "pressure", "all"), default="control",
                        help="control = 12 correctness tasks; pressure = large cold-haystack tasks; all = both")
    stress.add_argument("--output", type=Path, default=Path("evaluation-runs/local-stress"))
    stress.add_argument("--budget", type=int, default=4000)
    stress.add_argument("--repetitions", type=int, default=1,
                        help="Paired repetitions of every A/B/C task; use at least 3 for a real-model claim.")
    stress.add_argument("--min-paired-runs", type=int, default=36,
                        help="Minimum paired A/C observations before a provider-API result is claim-ready.")
    stress.add_argument("--price-file", type=Path, help="Versioned JSON price sheet for API cost estimates")
    swe = commands.add_parser("swebench-manifest", help="Write frozen eight-task SWE-bench Verified manifest")
    swe.add_argument("--output", type=Path, default=Path("evaluation-runs/swebench-verified-subset.json"))
    swe_inputs = commands.add_parser("swebench-inputs", help="Fetch eight official agent-safe SWE-bench task inputs")
    swe_inputs.add_argument("--output", type=Path, default=Path("evaluation-runs/swebench-verified-inputs.json"))
    swe_verify = commands.add_parser("swebench-verify-command", help="Print official verifier command for the frozen slice")
    swe_verify.add_argument("--predictions", type=Path, required=True)
    swe_verify.add_argument("--run-id", required=True)
    swe_verify.add_argument("--report-dir", type=Path, default=Path("evaluation-runs/swebench-verifier"))
    commands.add_parser("environment", help="Show optional Docker/SWE-bench prerequisites")
    preflight = commands.add_parser("preflight", help="Check real API evaluation readiness without sending a request")
    preflight.add_argument("--price-file", type=Path, help="Optional versioned price sheet to validate")
    fixture = commands.add_parser("fixture-api", help="Start a local OpenAI-compatible fixture API")
    fixture.add_argument("--host", default="127.0.0.1")
    fixture.add_argument("--port", type=int, default=8787)
    dashboard = commands.add_parser("dashboard", help="Start the local evaluation dashboard")
    dashboard.add_argument("--host", default="127.0.0.1")
    dashboard.add_argument("--port", type=int, default=0,
                           help="Loopback port; defaults to 0 so Windows selects a free port")
    dashboard.add_argument("--runs-root", type=Path, default=Path("evaluation-runs"))
    ablation = commands.add_parser("semantic-ablation",
                                   help="Run the deterministic C0/C1/C2 semantic-layer ablation (no model)")
    ablation.add_argument("--output", type=Path, default=Path("evaluation-runs/semantic-ablation"))
    ablation.add_argument("--budget", type=int, default=10_000,
                          help="Compiler byte budget; generous by default so completeness, not room, is measured")
    ablation.add_argument("--chart", action="store_true", help="Also render a PNG chart (requires matplotlib)")
    args = parser.parse_args(argv)
    if args.command == "environment":
        print(environment_status())
        return 0
    if args.command == "preflight":
        import json
        print(json.dumps(api_preflight(price_file=args.price_file), ensure_ascii=False, indent=2))
        return 0
    if args.command == "swebench-manifest":
        write_subset_manifest(args.output)
        print(args.output)
        return 0
    if args.command == "swebench-inputs":
        write_official_task_inputs(args.output, load_verified_subset())
        print(args.output)
        return 0
    if args.command == "swebench-verify-command":
        import subprocess
        print(subprocess.list2cmdline(verifier_command(args.predictions, run_id=args.run_id, report_dir=args.report_dir)))
        return 0
    if args.command == "fixture-api":
        serve_fixture_api(args.host, args.port)
        return 0
    if args.command == "dashboard":
        serve_dashboard(host=args.host, port=args.port, runs_root=args.runs_root)
        return 0
    if args.command == "semantic-ablation":
        from .semantic_ablation import run_ablation_suite
        results = run_ablation_suite(args.output, budget=args.budget)
        complete = sum(1 for r in results if r.outcomes["C2"]["closure_complete"])
        print(f"C2 closure complete: {complete}/{len(results)}; report: {args.output / 'ablation-report.md'}")
        if args.chart:
            from .semantic_chart import render_ablation_chart
            print(f"chart: {render_ablation_chart(args.output)}")
        return 0
    suite = {"control": STRESS_TASKS, "pressure": PRESSURE_TASKS, "all": ALL_TASKS}[args.suite]
    # The fixture model answers from whichever task set it is given, so it must
    # be seeded with the same suite that will be run.
    model = DeterministicEvidenceModel(suite) if args.model == "fixture" else OpenAICompatibleModel.from_environment()
    price_book = PriceBook.from_path(args.price_file) if args.price_file else None
    results = run_stress_suite(MinimalApiAgent(model, context_budget=args.budget, price_book=price_book), suite,
                               args.output, repetitions=args.repetitions,
                               evidence_kind="fixture" if args.model == "fixture" else "provider_api",
                               min_paired_runs=args.min_paired_runs)
    print(f"{sum(item.success for item in results)}/{len(results)} successful runs; report: {args.output / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
