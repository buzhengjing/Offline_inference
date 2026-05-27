"""Structured output schemas for all workflow nodes."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


# --- Seg1: Container + Env Exploration ---

class ModelWeightsResult(BaseModel):
    found: bool
    local_path: Optional[str] = None
    search_paths_checked: list[str] = Field(default_factory=list)


class GpuDetectionResult(BaseModel):
    vendor: str
    count: int
    model_name: str = ""


class BaseEnvResult(BaseModel):
    python_version: str
    cuda_version: str
    key_packages: dict[str, str] = Field(default_factory=dict)


class ModelFormatResult(BaseModel):
    format: str  # safetensors/bin/gguf/unknown


class ModelTypeResult(BaseModel):
    model_type: str  # causal_lm/vlm/embedding/reranker


class InferenceModeResult(BaseModel):
    inference_mode: str  # offline_native/custom_script
    inference_framework: str  # transformers/llama_cpp/custom_script
    custom_script_path: Optional[str] = None


# --- Seg2: Native Inference ---

class TestDataPlan(BaseModel):
    """LLM output: what test data to use."""
    source: str  # official/constructed
    rationale: str
    test_inputs: list[str]
    input_format: str  # text/json
    reference_url: Optional[str] = None


class InferenceScriptPlan(BaseModel):
    """LLM output: how to generate the inference script."""
    template_name: str  # causal_lm/vlm/embedding/reranker/llama_cpp/custom
    template_overrides: dict = Field(default_factory=dict)
    extra_imports: list[str] = Field(default_factory=list)
    special_handling: Optional[str] = None


class InferenceExecutionResult(BaseModel):
    exit_code: int
    stdout_tail: str
    stderr_tail: str
    duration_seconds: float
    output_files: list[str] = Field(default_factory=list)


class OutputValidationResult(BaseModel):
    valid: bool
    output_file_exists: bool
    output_non_empty: bool
    sample_output: Optional[str] = None
    error_summary: Optional[str] = None


class DiagnosisResult(BaseModel):
    """LLM output: failure diagnosis."""
    root_cause: str
    error_category: str  # oom/missing_dep/model_load/tokenizer/runtime/unknown
    fix_strategy: str
    retryable: bool
    script_patch: Optional[str] = None


class FixApplicationResult(BaseModel):
    """LLM output: applied fix details."""
    changes_made: str
    new_script_content: str
    expected_outcome: str


class ReadmeContent(BaseModel):
    """LLM output: README content."""
    title: str
    model_info: str
    environment_info: str
    usage_section: str
    inference_results: str
    flaggems_section: Optional[str] = None


class ReleaseResult(BaseModel):
    success: bool
    image_url: Optional[str] = None
    error: Optional[str] = None


# --- Seg3: FlagGems ---

class ComponentInstallResult(BaseModel):
    success: bool
    component: str
    version: str = ""
    error: Optional[str] = None


class ComponentVerifyResult(BaseModel):
    flaggems_ok: bool
    flaggems_version: str = ""
    flagtree_ok: bool
    flagtree_version: str = ""
