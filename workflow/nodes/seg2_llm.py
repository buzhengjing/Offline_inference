"""Seg2 LLM nodes — require Claude reasoning with structured output."""

from __future__ import annotations

from logging import Logger

from ..llm_node import LLMNode
from ..node import NodeResult
from ..retry import RetryPolicy
from ..schemas import DiagnosisResult, FixApplicationResult, InferenceScriptPlan, ReadmeContent, TestDataPlan
from ..state import WorkflowState


class QueryTestDataNode(LLMNode):
    def __init__(self):
        super().__init__(
            node_id="query_test_data",
            output_schema=TestDataPlan,
            timeout=300,
            retry_policy=RetryPolicy(max_retries=1),
            max_turns=5,
        )

    def build_prompt(self, state: WorkflowState) -> str:
        env_data = state._node_data.get("env_exploration", {})
        model_type = env_data.get("model_type", "causal_lm")
        model_name = state.model

        return f"""你是推理验证助手。请为模型 "{model_name}" 确定测试数据。

模型类型: {model_type}
模型路径: {state.model_path}

任务：
1. 根据模型名称，查找官方推荐的测试数据（如 HuggingFace 模型卡片中的示例）
2. 如果找不到官方数据，构造合适的测试输入
3. 输出格式根据模型类型决定：
   - causal_lm: 纯文本，每行一条 prompt
   - vlm: JSON 格式，包含 text + image 路径
   - embedding: 纯文本，每行一条待编码文本
   - reranker: JSON 格式，query + passage 对

请输出 TestDataPlan。"""

    def get_input_snapshot(self, state: WorkflowState) -> dict:
        return {"model": state.model, "model_type": state._node_data.get("env_exploration", {}).get("model_type")}


class GenerateInferenceScriptNode(LLMNode):
    def __init__(self):
        super().__init__(
            node_id="generate_inference_script",
            output_schema=InferenceScriptPlan,
            timeout=300,
            retry_policy=RetryPolicy(max_retries=2),
            max_turns=5,
        )

    def build_prompt(self, state: WorkflowState) -> str:
        env_data = state._node_data.get("env_exploration", {})
        model_type = env_data.get("model_type", "causal_lm")
        inference_mode = env_data.get("inference_mode", "offline_native")
        inference_framework = env_data.get("inference_framework", "transformers")
        custom_script_path = env_data.get("custom_script_path", "")

        available_templates = ["causal_lm", "vlm", "embedding", "reranker", "llama_cpp", "custom"]

        return f"""你是推理脚本生成助手。请为模型选择合适的推理脚本模板并提供参数。

模型: {state.model}
模型路径: {state.model_path}
模型类型: {model_type}
推理模式: {inference_mode}
推理框架: {inference_framework}
自带脚本路径: {custom_script_path or "无"}

可用模板: {available_templates}

任务：
1. 选择最合适的模板 (template_name)
2. 提供模板参数覆盖 (template_overrides)，如 model_class, tokenizer_class, max_new_tokens 等
3. 列出额外需要的 import 语句
4. 如有特殊处理逻辑（如 chat_template 适配），写在 special_handling 中

重要约束 — 输出文件完整性：
- embedding 模型：--output_file 必须保存每个样本的完整向量（所有维度），不能只保存前 N 维或摘要
- causal_lm / vlm 模型：--output_file 必须保存完整的生成文本，不能截断
- reranker 模型：--output_file 必须保存每对的完整 score
- 输出文件格式推荐 JSON/JSONL，便于后续校验

请输出 InferenceScriptPlan。"""


class DiagnoseFailureNode(LLMNode):
    def __init__(self):
        super().__init__(
            node_id="diagnose_failure",
            output_schema=DiagnosisResult,
            timeout=300,
            retry_policy=RetryPolicy(max_retries=1),
            max_turns=5,
        )

    def should_skip(self, state: WorkflowState) -> bool:
        validation = state._node_data.get("output_validation", {})
        return validation.get("valid", False)

    def build_prompt(self, state: WorkflowState) -> str:
        exec_result = state._node_data.get("execution_result", {})
        log_tail = state._node_data.get("log_tail", "")

        return f"""你是推理故障诊断专家。推理脚本执行失败，请分析原因。

退出码: {exec_result.get('exit_code', -1)}
执行时长: {exec_result.get('duration_seconds', 0):.1f}s

标准输出（最后50行）:
{exec_result.get('stdout_tail', '')}

标准错误（最后30行）:
{exec_result.get('stderr_tail', '')}

推理日志（最后100行）:
{log_tail}

请分析：
1. root_cause: 根本原因
2. error_category: oom/missing_dep/model_load/tokenizer/runtime/unknown
3. fix_strategy: 修复策略
4. retryable: 是否可以通过修改脚本重试
5. script_patch: 如果可修复，给出需要修改的代码片段

请输出 DiagnosisResult。"""


class ApplyFixNode(LLMNode):
    def __init__(self):
        super().__init__(
            node_id="apply_fix",
            output_schema=FixApplicationResult,
            timeout=300,
            retry_policy=RetryPolicy(max_retries=0),
            max_turns=5,
        )
        self.has_side_effects = True

    def should_skip(self, state: WorkflowState) -> bool:
        diagnosis = state._node_data.get("diagnosis", {})
        return not diagnosis.get("retryable", False)

    def build_prompt(self, state: WorkflowState) -> str:
        diagnosis = state._node_data.get("diagnosis", {})
        current_script = state._node_data.get("inference_script_content", "")

        return f"""你是推理脚本修复专家。请根据诊断结果修复推理脚本。

诊断结果:
- 根本原因: {diagnosis.get('root_cause', '')}
- 错误类别: {diagnosis.get('error_category', '')}
- 修复策略: {diagnosis.get('fix_strategy', '')}
- 建议补丁: {diagnosis.get('script_patch', '')}

当前脚本内容:
```python
{current_script}
```

请输出修复后的完整脚本（new_script_content 字段），以及修改说明。

请输出 FixApplicationResult。"""


class WriteReadmeNode(LLMNode):
    def __init__(self):
        super().__init__(
            node_id="write_readme",
            output_schema=ReadmeContent,
            timeout=300,
            retry_policy=RetryPolicy(max_retries=1),
            max_turns=5,
        )

    def build_prompt(self, state: WorkflowState) -> str:
        env_data = state._node_data.get("env_exploration", {})
        exec_result = state._node_data.get("execution_result", {})
        sample_output = state._node_data.get("output_validation", {}).get("sample_output", "")

        return f"""你是技术文档编写助手。请为推理验证结果编写 README.md。

模型: {state.model}
模型路径: {state.model_path}
推理框架: {env_data.get('inference_framework', '')}
Python 版本: {env_data.get('python_version', '')}
CUDA 版本: {env_data.get('cuda_version', '')}
推理时长: {exec_result.get('duration_seconds', 0):.1f}s

推理输出样例:
{sample_output[:500]}

请输出 ReadmeContent，包含：
- title: 标题
- model_info: 模型信息
- environment_info: 环境信息
- usage_section: 使用方法（包含运行命令）
- inference_results: 推理结果摘要"""
