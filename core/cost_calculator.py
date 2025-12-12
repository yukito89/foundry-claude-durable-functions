# モデル別の料金設定（USD per 1M tokens）
PRICING = {
    "gpt-5-mini": {
        "input": 0.250,
        "cached_input": 0.025,
        "output": 2.000
    },
    "gpt5.2": {
        "input": 1.750,
        "cached_input": 0.175,
        "output": 14.000
    }
}

def calculate_cost(usage_info: dict) -> float:
    """
    使用量情報からコストを計算
    
    Args:
        usage_info: {"input_tokens": int, "output_tokens": int, "model": str}
    
    Returns:
        float: コスト（USD）
    """
    model = usage_info.get("model", "")
    input_tokens = usage_info.get("input_tokens", 0)
    output_tokens = usage_info.get("output_tokens", 0)
    
    # モデル名からプライシング情報を取得
    pricing = None
    if model == "gpt-5-mini":
        pricing = PRICING["gpt-5-mini"]
    elif model == "gpt-5.2":
        pricing = PRICING["gpt5.2"]
    else:
        return 0.0
    
    # コスト計算（キャッシュは考慮せず通常料金で計算）
    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    total_cost = input_cost + output_cost
    
    return total_cost
