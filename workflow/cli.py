"""CLI entry point for the workflow runner.

Usage:
    python -m workflow.cli <target> <model> <harbor_user> <harbor_password> [options]

Options:
    --model-path PATH    Explicit model path
    --verbose            Enable verbose output
    --resume             Resume from last checkpoint
    --rerun-from STEP    Rerun from a specific step
    --timeout SECONDS    Total workflow timeout (default: 10800)
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path


def check_dependencies():
    missing = []
    try:
        import pydantic  # noqa: F401
    except ImportError:
        missing.append("pydantic>=2.0")
    try:
        import yaml  # noqa: F401
    except ImportError:
        missing.append("pyyaml>=6.0")

    if missing:
        print(f"[pre-flight] Installing missing dependencies: {missing}")
        import subprocess
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q"] + missing,
            check=True,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Offline inference pipeline workflow runner",
    )
    parser.add_argument("target", help="Docker image URL or container name")
    parser.add_argument("model", help="Model name (e.g., Qwen3-8B)")
    parser.add_argument("harbor_user", help="Harbor registry username")
    parser.add_argument("harbor_password", help="Harbor registry password")
    parser.add_argument("--model-path", default="", help="Explicit model path on host")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    parser.add_argument("--resume", action="store_true", help="Resume from last checkpoint")
    parser.add_argument("--rerun-from", default=None, help="Rerun from specific step ID")
    parser.add_argument("--timeout", type=float, default=10800, help="Total timeout in seconds")
    parser.add_argument("--use-nodes", action="store_true", help="Use fine-grained node execution mode")
    parser.add_argument(
        "--engine", choices=["dag", "state_machine"], default="dag",
        help="Execution engine: 'dag' (legacy linear) or 'state_machine' (state-driven)",
    )
    return parser.parse_args()


def _run_node_mode(state, args, checkpoint_mgr, logger, run_id, resume_from):
    """Run in fine-grained node mode (legacy DAG)."""
    from .build_graph import build_inference_graph
    from .node_runner import NodeRunner
    from .observability import TraceRecorder

    graph = build_inference_graph()
    trace_dir = Path(state.workspace_base) / "traces" / run_id
    trace_recorder = TraceRecorder(trace_dir)

    runner = NodeRunner(
        graph=graph,
        checkpoint_mgr=checkpoint_mgr,
        trace_recorder=trace_recorder,
        logger=logger,
        run_id=run_id,
        total_timeout=args.timeout,
    )

    return runner.run(state, resume_from=resume_from)


def _run_state_machine_mode(state, args, checkpoint_mgr, logger, run_id, entry_node):
    """Run in state-machine mode with condition edges and recovery."""
    from .build_graph import build_inference_state_graph
    from .observability import TraceRecorder
    from .state_machine_runner import StateMachineRunner

    graph = build_inference_state_graph()
    trace_dir = Path(state.workspace_base) / "traces" / run_id
    trace_recorder = TraceRecorder(trace_dir)

    runner = StateMachineRunner(
        graph=graph,
        checkpoint_mgr=checkpoint_mgr,
        trace_recorder=trace_recorder,
        logger=logger,
        run_id=run_id,
        total_timeout=args.timeout,
    )

    return runner.run(state, entry_node=entry_node)


def main():
    check_dependencies()

    from .checkpoint import CheckpointManager
    from .logging_setup import setup_logging
    from .runner import WorkflowRunner
    from .state import WorkflowState
    from .steps.container_prep import Seg1ContainerEnvStep, Seg1ValidateStep
    from .steps.flaggems import (
        Seg3InstallStep,
        Seg3InstallValidateStep,
        Seg3InferenceStep,
        Seg3InferenceValidateStep,
        Seg3ReleaseStep,
        Seg3ValidateStep,
    )
    from .steps.native_inference import Seg2NativeInferenceStep, Seg2ValidateStep
    from .steps.preflight import PreflightStep

    args = parse_args()

    # Generate run ID
    now = datetime.now(timezone.utc)
    model_safe = args.model.replace("/", "_")
    run_id = f"{now.strftime('%Y%m%d_%H%M%S')}_{model_safe}"

    # Paths
    project_root = Path(__file__).resolve().parent.parent
    workspace_base = f"/data/Offline_inference_workspace/{model_safe}"
    log_dir = f"{workspace_base}/logs/{run_id}"
    checkpoint_dir = Path(workspace_base) / "checkpoints"

    # Setup
    logger = setup_logging(Path(log_dir), run_id)
    checkpoint_mgr = CheckpointManager(checkpoint_dir)

    # Define steps
    steps = [
        PreflightStep(),
        Seg1ContainerEnvStep(),
        Seg1ValidateStep(),
        Seg2NativeInferenceStep(),
        Seg2ValidateStep(),
        Seg3InstallStep(),
        Seg3InstallValidateStep(),
        Seg3InferenceStep(),
        Seg3InferenceValidateStep(),
        Seg3ReleaseStep(),
        Seg3ValidateStep(),
    ]

    # Build or restore state
    resume_from = None
    if args.resume:
        loaded = checkpoint_mgr.load_latest()
        if loaded:
            state, last_step = loaded
            # Find next step after last completed
            step_ids = [s.name for s in steps]
            idx = step_ids.index(last_step) if last_step in step_ids else -1
            if idx + 1 < len(step_ids):
                resume_from = step_ids[idx + 1]
            logger.info(
                f"Resuming from checkpoint after '{last_step}', next step: {resume_from}",
                extra={"step": "init", "run_id": run_id},
            )
        else:
            logger.warning(
                "No checkpoint found, starting fresh",
                extra={"step": "init", "run_id": run_id},
            )
            state = None
    elif args.rerun_from:
        loaded = checkpoint_mgr.load_latest()
        if loaded:
            state, _ = loaded
            resume_from = args.rerun_from
            # Reset the target step and all after it
            step_ids = [s.name for s in steps]
            idx = step_ids.index(args.rerun_from) if args.rerun_from in step_ids else 0
            from .state import StepStatus
            for rec in state.steps[idx:]:
                rec.status = StepStatus.PENDING
                rec.started_at = None
                rec.finished_at = None
                rec.duration_seconds = None
                rec.error = None
        else:
            state = None
            resume_from = None
    else:
        state = None

    if state is None:
        state = WorkflowState(
            run_id=run_id,
            target=args.target,
            model=args.model,
            model_path=args.model_path or None,
            harbor_user=args.harbor_user,
            harbor_password=args.harbor_password,
            verbose=args.verbose,
            model_safe=model_safe,
            workspace_base=workspace_base,
            log_dir=log_dir,
            project_root=str(project_root),
        )
        state.init_steps([s.name for s in steps])

    # Run
    if args.engine == "state_machine" or (args.use_nodes and args.engine != "dag"):
        final_state = _run_state_machine_mode(state, args, checkpoint_mgr, logger, run_id, resume_from)
    elif args.use_nodes:
        final_state = _run_node_mode(state, args, checkpoint_mgr, logger, run_id, resume_from)
    else:
        runner = WorkflowRunner(
            steps=steps,
            checkpoint_mgr=checkpoint_mgr,
            logger=logger,
            run_id=run_id,
            total_timeout=args.timeout,
        )
        final_state = runner.run(state, resume_from=resume_from)

    # Exit code
    if final_state.workflow_terminated:
        sys.exit(1)
    failed = any(r.status.value == "failed" for r in final_state.steps)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
