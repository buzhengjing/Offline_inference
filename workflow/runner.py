"""WorkflowRunner — orchestrates step execution with retry, checkpoint, and logging."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from .checkpoint import CheckpointManager
from .logging_setup import StepLogger
from .retry import RetryPolicy
from .state import StepStatus, WorkflowState
from .step import WorkflowStep
from .timeout import StepTimeoutError, check_workflow_timeout, step_timeout, workflow_timeout


class WorkflowRunner:
    def __init__(
        self,
        steps: list[WorkflowStep],
        checkpoint_mgr: CheckpointManager,
        logger: logging.Logger,
        run_id: str,
        total_timeout: float = 10800,  # 3 hours
    ):
        self.steps = steps
        self.checkpoint_mgr = checkpoint_mgr
        self.logger = logger
        self.run_id = run_id
        self.total_timeout = total_timeout

    def run(
        self,
        state: WorkflowState,
        resume_from: Optional[str] = None,
    ) -> WorkflowState:
        state.started_at = datetime.now(timezone.utc)
        step_ids = [s.name for s in self.steps]

        # If resuming, find the step to resume from
        skip_until_found = resume_from is not None
        resume_index = 0
        if resume_from:
            for i, s in enumerate(self.steps):
                if s.name == resume_from:
                    resume_index = i
                    break

        with workflow_timeout(self.total_timeout):
            for i, step in enumerate(self.steps):
                if skip_until_found and i < resume_index:
                    continue
                skip_until_found = False

                step_record = state.get_step(step.name)
                if not step_record:
                    continue

                # Skip already-completed steps when resuming
                if step_record.status == StepStatus.SUCCESS:
                    continue

                # Check global workflow timeout between steps
                try:
                    check_workflow_timeout(step.name)
                except StepTimeoutError:
                    state.workflow_terminated = True
                    state.termination_reason = f"Total workflow timeout ({self.total_timeout}s) exceeded"
                    break

                if step.should_skip(state):
                    step_record.status = StepStatus.SKIPPED
                    self.checkpoint_mgr.save(state, step.name)
                    continue

                state.current_step = step.name
                state = self._execute_with_retry(step, state)
                self.checkpoint_mgr.save(state, step.name)

                if state.workflow_terminated:
                    self.logger.info(
                        f"Workflow terminated at step '{step.name}': {state.termination_reason}",
                        extra={"step": step.name, "run_id": self.run_id},
                    )
                    break

                if step_record.status == StepStatus.FAILED:
                    self.logger.error(
                        f"Step '{step.name}' failed after retries: {step_record.error}",
                        extra={"step": step.name, "run_id": self.run_id},
                    )
                    break

        state.finished_at = datetime.now(timezone.utc)
        state.current_step = None
        self.checkpoint_mgr.save(state, "final")
        self._print_summary(state)
        return state

    def _execute_with_retry(self, step: WorkflowStep, state: WorkflowState) -> WorkflowState:
        step_record = state.get_step(step.name)
        step_log = StepLogger(self.logger, step.name, self.run_id)
        policy = step.retry_policy

        for attempt in range(policy.max_retries + 1):
            step_record.mark_running()
            step_record.retry_count = attempt
            step_log.info(f"Starting (attempt {attempt + 1}/{policy.max_retries + 1})")

            try:
                with step_timeout(step.name, step.timeout):
                    state = step.execute(state, self.logger)
                step_record.mark_success()
                step_log.info(f"Completed in {step_record.duration_seconds:.1f}s")
                return state

            except StepTimeoutError as e:
                error_msg = str(e)
                step_log.error(f"Timeout: {error_msg}")
                if policy.should_retry(attempt, error_msg):
                    policy.wait(attempt)
                    continue
                step_record.mark_failed(error_msg)
                return state

            except Exception as e:
                error_msg = str(e)
                step_log.error(f"Error: {error_msg}")
                if policy.should_retry(attempt, error_msg):
                    step_log.info(f"Retrying after {policy.delay(attempt):.1f}s...")
                    policy.wait(attempt)
                    continue
                step_record.mark_failed(error_msg)
                return state

        return state

    def _print_summary(self, state: WorkflowState) -> None:
        total = 0.0
        print("\n" + "=" * 60)
        print("  Workflow Summary")
        print("=" * 60)
        for rec in state.steps:
            dur = rec.duration_seconds or 0
            total += dur
            status_icon = {"success": "✓", "failed": "✗", "skipped": "⊘"}.get(rec.status.value, "?")
            print(f"  {status_icon} {rec.step_id:<30} {dur:>6.0f}s  [{rec.status.value}]")
        print("-" * 60)
        print(f"  Total: {total:.0f}s ({total/60:.1f}m)")
        if state.workflow_terminated:
            print(f"  Terminated: {state.termination_reason}")
        print("=" * 60 + "\n")
