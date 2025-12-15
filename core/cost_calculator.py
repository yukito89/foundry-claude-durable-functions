# モデル別の料金設定（USD per 1M tokens）
PRICING = {
    "claude-haiku-4-5": {
        "input": 1.0,
        "output": 5.0
    },
    "claude-sonnet-4-5": {
        "input": 3.0,
        "output": 15.0
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
    pricing = PRICING.get(model)
    if not pricing:
        return 0.0
    
    # コスト計算
    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    
    return input_cost + output_cost
