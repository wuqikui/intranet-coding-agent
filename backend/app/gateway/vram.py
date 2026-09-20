"""显存自适应估算。

基于模型参数量与量化等级估算 KV cache 占用、推荐模型配置、最大并发数。
所有估算为经验值，用于内网部署时的容量规划参考。
"""
import logging

logger = logging.getLogger("agent.gateway.vram")


# 量化等级 → 每参数占用字节数
_QUANT_BYTES = {
    "fp16": 2.0,   # FP16 / BF16
    "fp8": 1.0,
    "q8": 1.0,
    "q5": 0.625,
    "q4": 0.5,
    "awq": 0.5,
    "gptq": 0.5,
}


def _quant_to_bytes(quantization: str) -> float:
    """量化等级 → 每参数字节数。"""
    return _QUANT_BYTES.get(quantization.lower(), 2.0)


# 已知模型规格：(参数量B → (层数, 隐藏维度))，基于 LLaMA/Qwen 系列实参
_KNOWN_ARCH = {
    7.0: (32, 4096),
    14.0: (40, 5120),
    32.0: (64, 5120),
    70.0: (80, 8192),
}


def _estimate_arch(model_params_b: float):
    """用参数量估算层数与隐藏维度。

    优先查已知规格表；未知规格按最近已知点幂律缩放（经验近似）。
    返回 (num_layers, hidden_dim)。
    """
    import math

    if model_params_b <= 0:
        return 0, 0
    # 命中已知规格
    if model_params_b in _KNOWN_ARCH:
        return _KNOWN_ARCH[model_params_b]
    # 找最近的已知规格并幂律缩放
    nearest = min(_KNOWN_ARCH, key=lambda k: abs(k - model_params_b))
    base_l, base_h = _KNOWN_ARCH[nearest]
    scale = model_params_b / nearest
    num_layers = max(1, int(base_l * (scale ** 0.4)))
    hidden_dim = max(1, int(base_h * (scale ** 0.3)))
    return num_layers, hidden_dim


def estimate_kv_cache_vram(
    model_params_b: float, quantization: str, context_len: int
) -> float:
    """估算 KV cache 显存（GB）。

    KV cache ≈ 2 (K & V) * num_layers * 2 (batch=1) * hidden_dim * context_len * dtype_size
    简化：用模型参数量估算 num_layers 与 hidden_dim。
    """
    num_layers, hidden_dim = _estimate_arch(model_params_b)
    if num_layers == 0 or hidden_dim == 0:
        return 0.0
    # KV 通常与模型权重同精度
    dtype_size = _quant_to_bytes(quantization)
    # 字节数 → GB
    bytes_total = 2 * num_layers * 2 * hidden_dim * context_len * dtype_size
    return bytes_total / 1e9


def _model_vram_gb(model_params_b: float, quantization: str) -> float:
    """估算模型权重占用显存（GB）。"""
    dtype_size = _quant_to_bytes(quantization)
    return (model_params_b * 1e9 * dtype_size) / 1e9


# 预置模型规格表：(参数量B, 量化, 估算权重显存GB)
_MODEL_SPECS = {
    "7b_q4": (7.0, "q4", _model_vram_gb(7.0, "q4")),
    "14b_q8": (14.0, "q8", _model_vram_gb(14.0, "q8")),
    "32b_q4": (32.0, "q4", _model_vram_gb(32.0, "q4")),
    "70b_q4": (70.0, "q4", _model_vram_gb(70.0, "q4")),
}


def recommend_model(gpu_count: int, vram_per_gpu_gb: float) -> dict:
    """根据 GPU 数量与单卡显存推荐模型配置。

    Args:
        gpu_count: GPU 数量（1=单卡，>=2=多卡）
        vram_per_gpu_gb: 单卡显存（GB）

    Returns:
        {"model": str, "quantization": str, "estimated_vram_gb": float, "tp_size": int}
    """
    if gpu_count <= 1:
        # 单卡策略
        if vram_per_gpu_gb >= 40:
            spec = _MODEL_SPECS["32b_q4"]
        elif vram_per_gpu_gb >= 20:
            spec = _MODEL_SPECS["14b_q8"]
        else:
            spec = _MODEL_SPECS["7b_q4"]
        tp_size = 1
    else:
        # 多卡策略：按总显存
        vram_total = vram_per_gpu_gb * gpu_count
        if vram_total >= 160:
            spec = _MODEL_SPECS["70b_q4"]
        elif vram_total >= 80:
            spec = _MODEL_SPECS["32b_q4"]
        else:
            spec = _MODEL_SPECS["7b_q4"]
        # TP 规模按总显存 / 单模型显存向上取整，封顶为 gpu_count
        import math
        tp_size = min(gpu_count, max(1, math.ceil(spec[2] / vram_per_gpu_gb)))

    params_b, quant, est_vram = spec
    # 友好的模型名
    model_name = f"qwen2.5-coder-{int(params_b)}b-instruct"
    logger.info(
        "推荐: gpu=%d vram_per=%.1fGB → %s %s (≈%.1fGB, tp=%d)",
        gpu_count, vram_per_gpu_gb, model_name, quant, est_vram, tp_size,
    )
    return {
        "model": model_name,
        "quantization": quant,
        "estimated_vram_gb": round(est_vram, 2),
        "tp_size": tp_size,
    }


def max_concurrent(
    gpu_count: int,
    vram_per_gpu_gb: float,
    model_vram_gb: float,
    kv_per_request_gb: float,
) -> int:
    """按剩余显存 / KV per request 估算并发数。

    剩余显存 = 总显存 - 模型权重显存 - 预留（KV_CACHE_RESERVE 比例由调用方折算）。
    并发数 = max(1, 剩余显存 / kv_per_request_gb)。
    """
    total_vram = gpu_count * vram_per_gpu_gb
    free_vram = total_vram - model_vram_gb
    if free_vram <= 0 or kv_per_request_gb <= 0:
        return 1
    concurrent = int(free_vram / kv_per_request_gb)
    return max(1, concurrent)
