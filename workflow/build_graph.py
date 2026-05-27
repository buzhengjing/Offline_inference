"""Build the complete node graph for the offline inference pipeline."""

from __future__ import annotations

from .failure_policy import FailureCategory, FailureRoute
from .graph import NodeGraph
from .nodes.seg1_container import (
    CreateContainerNode,
    DeployWorkspaceNode,
    DetectGpuNode,
    InitContextNode,
    SearchModelWeightsNode,
    ValidateSeg1Node,
)
from .nodes.seg1_env import (
    DetectBaseEnvNode,
    DetectInferenceModeNode,
    DetectModelFormatNode,
    DetectModelTypeNode,
    WriteEnvContextNode,
)
from .nodes.seg2_inference import (
    CollectExecutionLogsNode,
    DeployInferenceScriptNode,
    DeployReadmeNode,
    ExecuteInferenceNode,
    ReadEnvContextNode,
    ReleaseNativeImageNode,
    UpdateNativeContextNode,
    ValidateInferenceOutputNode,
    ValidateSeg2Node,
)
from .nodes.seg2_llm import (
    ApplyFixNode,
    DiagnoseFailureNode,
    GenerateInferenceScriptNode,
    QueryTestDataNode,
    WriteReadmeNode,
)
from .nodes.seg3_flaggems import (
    CollectFlaggemsLogsNode,
    DeployReadmeFlaggemsNode,
    ExecuteFlaggemsInferenceNode,
    InstallFlaggemsNode,
    InstallFlagtreeNode,
    ReleaseFlaggemsImageNode,
    UpdateFlaggemsContextNode,
    ValidateFlaggemsOutputNode,
    ValidateSeg3Node,
    VerifyComponentsNode,
)
from .nodes.seg3_llm import (
    ApplyFlaggemsFixNode,
    DiagnoseFlaggemsFailureNode,
    UpdateReadmeFlaggemsNode,
)
from .recovery import NodeSemantics, RecoveryAction, RecoveryPolicy
from .state_graph import StateGraph
from .termination import TerminationPolicy


def build_inference_graph() -> NodeGraph:
    """Construct the full node DAG for the offline inference pipeline."""
    graph = NodeGraph()

    # --- Seg1: Container + Env Exploration ---
    graph.add_node(SearchModelWeightsNode())
    graph.add_node(DetectGpuNode())
    graph.add_node(CreateContainerNode())
    graph.add_node(DeployWorkspaceNode())
    graph.add_node(InitContextNode())
    graph.add_node(DetectBaseEnvNode())
    graph.add_node(DetectModelFormatNode())
    graph.add_node(DetectModelTypeNode())
    graph.add_node(DetectInferenceModeNode())
    graph.add_node(WriteEnvContextNode())
    graph.add_node(ValidateSeg1Node())

    # Seg1 edges
    graph.add_edge("search_model_weights", "detect_gpu")
    graph.add_edge("detect_gpu", "create_container")
    graph.add_edge("create_container", "deploy_workspace")
    graph.add_edge("deploy_workspace", "init_context")
    graph.add_edge("init_context", "detect_base_env")
    graph.add_edge("init_context", "detect_model_format")
    graph.add_edge("init_context", "detect_model_type")
    graph.add_edge("detect_base_env", "detect_inference_mode")
    graph.add_edge("detect_model_format", "detect_inference_mode")
    graph.add_edge("detect_model_type", "detect_inference_mode")
    graph.add_edge("detect_inference_mode", "write_env_context")
    graph.add_edge("write_env_context", "validate_seg1")

    # --- Seg2: Native Inference ---
    graph.add_node(ReadEnvContextNode())
    graph.add_node(QueryTestDataNode())
    graph.add_node(GenerateInferenceScriptNode())
    graph.add_node(DeployInferenceScriptNode())
    graph.add_node(ExecuteInferenceNode())
    graph.add_node(CollectExecutionLogsNode())
    graph.add_node(ValidateInferenceOutputNode())
    graph.add_node(DiagnoseFailureNode())
    graph.add_node(ApplyFixNode())
    graph.add_node(WriteReadmeNode())
    graph.add_node(DeployReadmeNode())
    graph.add_node(UpdateNativeContextNode())
    graph.add_node(ReleaseNativeImageNode())
    graph.add_node(ValidateSeg2Node())

    # Seg2 edges
    graph.add_edge("validate_seg1", "read_env_context")
    graph.add_edge("read_env_context", "query_test_data")
    graph.add_edge("query_test_data", "generate_inference_script")
    graph.add_edge("generate_inference_script", "deploy_inference_script")
    graph.add_edge("deploy_inference_script", "execute_inference")
    graph.add_edge("execute_inference", "collect_execution_logs")
    graph.add_edge("collect_execution_logs", "validate_inference_output")
    graph.add_edge("validate_inference_output", "diagnose_failure", condition="inference_failed")
    graph.add_edge("validate_inference_output", "write_readme", condition="inference_passed")
    graph.add_edge("diagnose_failure", "apply_fix")
    # Note: apply_fix → execute_inference retry is handled by NodeRunner, not as a DAG edge
    graph.add_edge("apply_fix", "write_readme")  # after fix applied, proceed to readme
    graph.add_edge("write_readme", "deploy_readme")
    graph.add_edge("deploy_readme", "update_native_context")
    graph.add_edge("update_native_context", "release_native_image")
    graph.add_edge("release_native_image", "validate_seg2")

    # --- Seg3: FlagGems ---
    graph.add_node(InstallFlaggemsNode())
    graph.add_node(InstallFlagtreeNode())
    graph.add_node(VerifyComponentsNode())
    graph.add_node(ExecuteFlaggemsInferenceNode())
    graph.add_node(CollectFlaggemsLogsNode())
    graph.add_node(ValidateFlaggemsOutputNode())
    graph.add_node(DiagnoseFlaggemsFailureNode())
    graph.add_node(ApplyFlaggemsFixNode())
    graph.add_node(UpdateReadmeFlaggemsNode())
    graph.add_node(DeployReadmeFlaggemsNode())
    graph.add_node(UpdateFlaggemsContextNode())
    graph.add_node(ReleaseFlaggemsImageNode())
    graph.add_node(ValidateSeg3Node())

    # Seg3 edges
    graph.add_edge("validate_seg2", "install_flaggems")
    graph.add_edge("install_flaggems", "install_flagtree")
    graph.add_edge("install_flagtree", "verify_components")
    graph.add_edge("verify_components", "execute_flaggems_inference")
    graph.add_edge("execute_flaggems_inference", "collect_flaggems_logs")
    graph.add_edge("collect_flaggems_logs", "validate_flaggems_output")
    graph.add_edge("validate_flaggems_output", "diagnose_flaggems_failure", condition="flaggems_failed")
    graph.add_edge("validate_flaggems_output", "update_readme_flaggems", condition="flaggems_passed")
    graph.add_edge("diagnose_flaggems_failure", "apply_flaggems_fix")
    # Note: apply_flaggems_fix → execute_flaggems_inference retry is handled by NodeRunner
    graph.add_edge("apply_flaggems_fix", "update_readme_flaggems")
    graph.add_edge("update_readme_flaggems", "deploy_readme_flaggems")
    graph.add_edge("deploy_readme_flaggems", "update_flaggems_context")
    graph.add_edge("update_flaggems_context", "release_flaggems_image")
    graph.add_edge("release_flaggems_image", "validate_seg3")

    return graph


def build_inference_state_graph() -> StateGraph:
    """Construct the state-machine-style graph with condition edges and recovery."""
    graph = StateGraph()

    # --- Register all nodes (same as DAG) ---
    graph.add_node(SearchModelWeightsNode())
    graph.add_node(DetectGpuNode())
    graph.add_node(CreateContainerNode())
    graph.add_node(DeployWorkspaceNode())
    graph.add_node(InitContextNode())
    graph.add_node(DetectBaseEnvNode())
    graph.add_node(DetectModelFormatNode())
    graph.add_node(DetectModelTypeNode())
    graph.add_node(DetectInferenceModeNode())
    graph.add_node(WriteEnvContextNode())
    graph.add_node(ValidateSeg1Node())
    graph.add_node(ReadEnvContextNode())
    graph.add_node(QueryTestDataNode())
    graph.add_node(GenerateInferenceScriptNode())
    graph.add_node(DeployInferenceScriptNode())
    graph.add_node(ExecuteInferenceNode())
    graph.add_node(CollectExecutionLogsNode())
    graph.add_node(ValidateInferenceOutputNode())
    graph.add_node(DiagnoseFailureNode())
    graph.add_node(ApplyFixNode())
    graph.add_node(WriteReadmeNode())
    graph.add_node(DeployReadmeNode())
    graph.add_node(UpdateNativeContextNode())
    graph.add_node(ReleaseNativeImageNode())
    graph.add_node(ValidateSeg2Node())
    graph.add_node(InstallFlaggemsNode())
    graph.add_node(InstallFlagtreeNode())
    graph.add_node(VerifyComponentsNode())
    graph.add_node(ExecuteFlaggemsInferenceNode())
    graph.add_node(CollectFlaggemsLogsNode())
    graph.add_node(ValidateFlaggemsOutputNode())
    graph.add_node(DiagnoseFlaggemsFailureNode())
    graph.add_node(ApplyFlaggemsFixNode())
    graph.add_node(UpdateReadmeFlaggemsNode())
    graph.add_node(DeployReadmeFlaggemsNode())
    graph.add_node(UpdateFlaggemsContextNode())
    graph.add_node(ReleaseFlaggemsImageNode())
    graph.add_node(ValidateSeg3Node())

    graph.set_entry("search_model_weights")

    # --- Seg1: Linear chain ---
    graph.add_edge("search_model_weights", "detect_gpu")
    graph.add_edge("detect_gpu", "create_container")
    graph.add_edge("create_container", "deploy_workspace")
    graph.add_edge("deploy_workspace", "init_context")
    graph.add_edge("init_context", "detect_base_env")
    graph.add_edge("detect_base_env", "detect_model_format")
    graph.add_edge("detect_model_format", "detect_model_type")
    graph.add_edge("detect_model_type", "detect_inference_mode")

    # Branching: inference mode determines next path
    graph.add_condition_edge(
        "detect_inference_mode", "write_env_context",
        condition=lambda s: True,
        label="default_path",
    )
    graph.add_edge("write_env_context", "validate_seg1")

    # --- Seg2: Native Inference with condition edges ---
    graph.add_edge("validate_seg1", "read_env_context")
    graph.add_edge("read_env_context", "query_test_data")
    graph.add_edge("query_test_data", "generate_inference_script")
    graph.add_edge("generate_inference_script", "deploy_inference_script")
    graph.add_edge("deploy_inference_script", "execute_inference")
    graph.add_edge("execute_inference", "collect_execution_logs")
    graph.add_edge("collect_execution_logs", "validate_inference_output")

    # Condition edge: inference result determines next node
    graph.add_condition_edge(
        "validate_inference_output", "write_readme",
        condition=lambda s: s.get_node_data("output_validation", {}).get("valid", False),
        priority=10,
        label="inference_passed",
    )
    graph.add_condition_edge(
        "validate_inference_output", "diagnose_failure",
        condition=lambda s: not s.get_node_data("output_validation", {}).get("valid", False),
        priority=5,
        label="inference_failed",
    )

    graph.add_edge("diagnose_failure", "apply_fix")
    graph.add_edge("apply_fix", "write_readme")
    graph.add_edge("write_readme", "deploy_readme")
    graph.add_edge("deploy_readme", "update_native_context")
    graph.add_edge("update_native_context", "release_native_image")
    graph.add_edge("release_native_image", "validate_seg2")

    # --- Seg3: FlagGems with condition edges ---
    graph.add_edge("validate_seg2", "install_flaggems")
    graph.add_edge("install_flaggems", "install_flagtree")
    graph.add_edge("install_flagtree", "verify_components")
    graph.add_edge("verify_components", "execute_flaggems_inference")
    graph.add_edge("execute_flaggems_inference", "collect_flaggems_logs")
    graph.add_edge("collect_flaggems_logs", "validate_flaggems_output")

    graph.add_condition_edge(
        "validate_flaggems_output", "update_readme_flaggems",
        condition=lambda s: s.get_node_data("flaggems_validation", {}).get("valid", False),
        priority=10,
        label="flaggems_passed",
    )
    graph.add_condition_edge(
        "validate_flaggems_output", "diagnose_flaggems_failure",
        condition=lambda s: not s.get_node_data("flaggems_validation", {}).get("valid", False),
        priority=5,
        label="flaggems_failed",
    )

    graph.add_edge("diagnose_flaggems_failure", "apply_flaggems_fix")
    graph.add_edge("apply_flaggems_fix", "update_readme_flaggems")
    graph.add_edge("update_readme_flaggems", "deploy_readme_flaggems")
    graph.add_edge("deploy_readme_flaggems", "update_flaggems_context")
    graph.add_edge("update_flaggems_context", "release_flaggems_image")
    graph.add_edge("release_flaggems_image", "validate_seg3")

    # --- Failure routing ---
    graph.set_failure_routes("execute_inference", [
        FailureRoute(FailureCategory.OOM, "diagnose_failure", "retry_with_fix", max_attempts=2),
        FailureRoute(FailureCategory.MISSING_DEPENDENCY, "diagnose_failure", "install_dep"),
        FailureRoute(FailureCategory.MODEL_CORRUPTION, "__terminate__", "terminate"),
        FailureRoute(FailureCategory.TIMEOUT, "diagnose_failure", "retry_with_fix"),
    ])
    graph.set_failure_routes("execute_flaggems_inference", [
        FailureRoute(FailureCategory.OOM, "diagnose_flaggems_failure", "retry_with_fix"),
        FailureRoute(FailureCategory.MISSING_DEPENDENCY, "diagnose_flaggems_failure", "install_dep"),
        FailureRoute(FailureCategory.MODEL_CORRUPTION, "__terminate__", "terminate"),
    ])

    # --- Recovery policies ---
    graph.set_recovery(RecoveryPolicy(
        node_id="execute_inference",
        on_failure=RecoveryAction.RETRY_CURRENT,
        max_retries=2,
    ))
    graph.set_recovery(RecoveryPolicy(
        node_id="install_flaggems",
        on_failure=RecoveryAction.SKIP_AND_CONTINUE,
        is_optional=True,
    ))
    graph.set_recovery(RecoveryPolicy(
        node_id="install_flagtree",
        on_failure=RecoveryAction.SKIP_AND_CONTINUE,
        is_optional=True,
    ))
    graph.set_recovery(RecoveryPolicy(
        node_id="execute_flaggems_inference",
        on_failure=RecoveryAction.SKIP_AND_CONTINUE,
        is_optional=True,
    ))
    graph.set_recovery(RecoveryPolicy(
        node_id="diagnose_failure",
        on_failure=RecoveryAction.SKIP_AND_CONTINUE,
        is_optional=True,
    ))
    graph.set_recovery(RecoveryPolicy(
        node_id="apply_fix",
        on_failure=RecoveryAction.SKIP_AND_CONTINUE,
        is_optional=True,
    ))

    # --- Node semantics ---
    for nid in ["detect_gpu", "detect_base_env", "detect_model_format",
                "detect_model_type", "detect_inference_mode", "read_env_context",
                "validate_seg1", "validate_seg2", "validate_seg3"]:
        graph.set_semantics(nid, NodeSemantics(idempotent=True))

    graph.set_semantics("execute_inference", NodeSemantics(has_side_effects=True))
    graph.set_semantics("release_native_image", NodeSemantics(
        has_side_effects=True, side_effect_boundary=True,
    ))
    graph.set_semantics("release_flaggems_image", NodeSemantics(
        has_side_effects=True, side_effect_boundary=True,
    ))

    # --- Termination policy ---
    graph.termination_policy = TerminationPolicy(
        max_consecutive_failures=3,
        max_total_failures=5,
        fatal_categories={FailureCategory.MODEL_CORRUPTION},
        ignorable_nodes={
            "diagnose_failure", "apply_fix",
            "diagnose_flaggems_failure", "apply_flaggems_fix",
        },
        partial_success_segments={"seg3", "flaggems"},
    )

    return graph
