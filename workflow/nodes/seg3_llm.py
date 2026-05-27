"""Seg3 LLM nodes for FlagGems failure diagnosis and README update."""

from __future__ import annotations

from logging import Logger

from ..llm_node import LLMNode
from ..node import NodeResult
from ..retry import RetryPolicy
from ..schemas import DiagnosisResult, FixApplicationResult, ReadmeContent
from ..state import WorkflowState


class DiagnoseFlaggemsFailureNode(LLMNode):
    def __init__(self):
        super().__init__(
            node_id="diagnose_flaggems_failure",
            output_schema=DiagnosisResult,
            timeout=300,
            retry_policy=RetryPolicy(max_retries=1),
            max_turns=5,
        )

    def should_skip(self, state: WorkflowState) -> bool:
        if not state.native_inference_ok:
            return True
        validation = state._node_data.get("flaggems_output_validation", {})
        return validation.get("valid", False)

    def build_prompt(self, state: WorkflowState) -> str:
        exec_result = state._node_data.get("flaggems_execution_result", {})
        log_tail = state._node_data.get("flaggems_log_tail", "")

        return f"""你是 FlagGems 推理故障诊断专家。FlagGems 推理执行失败，请分析原因。

注意：这是在 FlagGems 环境下的推理失败，原生推理已经成功。
问题可能与 FlagGems/FlagTree 兼容性有关。

退出码: {exec_result.get('exit_code', -1)}
执行时长: {exec_result.get('duration_seconds', 0):.1f}s

标准输出（最后50行）:
{exec_result.get('stdout_tail', '')}

标准错误:
{exec_result.get('stderr_tail', '')}

推理日志:
{log_tail}

请分析：
1. root_cause: 根本原因（重点关注 FlagGems 兼容性）
2. error_category: oom/missing_dep/model_load/tokenizer/runtime/unknown
3. fix_strategy: 修复策略
4. retryable: 是否可以通过修改脚本重试
5. script_patch: 如果可修复，给出需要修改的代码片段

请输出 DiagnosisResult。"""


class ApplyFlaggemsFixNode(LLMNode):
    def __init__(self):
        super().__init__(
            node_id="apply_flaggems_fix",
            output_schema=FixApplicationResult,
            timeout=300,
            retry_policy=RetryPolicy(max_retries=0),
            max_turns=5,
        )
        self.has_side_effects = True

    def should_skip(self, state: WorkflowState) -> bool:
        if not state.native_inference_ok:
            return True
        diagnosis = state._node_data.get("flaggems_diagnosis", {})
        return not diagnosis.get("retryable", False)

    def build_prompt(self, state: WorkflowState) -> str:
        diagnosis = state._node_data.get("flaggems_diagnosis", {})
        current_script = state._node_data.get("inference_script_content", "")

        return f"""你是 FlagGems 推理脚本修复专家。请根据诊断结果修复推理脚本。

诊断结果:
- 根本原因: {diagnosis.get('root_cause', '')}
- 错误类别: {diagnosis.get('error_category', '')}
- 修复策略: {diagnosis.get('fix_strategy', '')}
- 建议补丁: {diagnosis.get('script_patch', '')}

当前脚本内容:
```python
{current_script}
```

注意：脚本顶部的 flag_gems try/except 块必须保留。

请输出修复后的完整脚本（new_script_content 字段），以及修改说明。

请输出 FixApplicationResult。"""


class UpdateReadmeFlaggemsNode(LLMNode):
    def __init__(self):
        super().__init__(
            node_id="update_readme_flaggems",
            output_schema=ReadmeContent,
            timeout=300,
            retry_policy=RetryPolicy(max_retries=1),
            max_turns=5,
        )

    def should_skip(self, state: WorkflowState) -> bool:
        return not state.native_inference_ok

    def build_prompt(self, state: WorkflowState) -> str:
        existing_readme = state._node_data.get("readme_content", "")
        flaggems_result = state._node_data.get("flaggems_execution_result", {})
        gems_txt = state._node_data.get("gems_txt_content", "")
        component_versions = state._node_data.get("component_versions", {})

        return f"""你是技术文档编写助手。请在现有 README 基础上追加 FlagGems 验证结果。

现有 README:
{existing_readme[:2000]}

FlagGems 版本: {component_versions.get('flaggems_version', '')}
FlagTree 版本: {component_versions.get('flagtree_version', '')}
推理时长: {flaggems_result.get('duration_seconds', 0):.1f}s

gems.txt 内容（FlagGems 算子记录）:
{gems_txt[:1000]}

请输出完整的 ReadmeContent，在 flaggems_section 字段中包含：
- FlagGems/FlagTree 版本信息
- FlagGems 推理验证结果
- 使用的算子列表（来自 gems.txt）
- 运行命令"""
