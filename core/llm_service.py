import logging
import os
import time
from anthropic import AnthropicFoundry
from dotenv import load_dotenv

from prompts import (
    STRUCTURING_PROMPT, 
    EXTRACT_TEST_PERSPECTIVES_PROMPT, 
    CREATE_TEST_SPEC_PROMPT_SIMPLE,
    CREATE_TEST_SPEC_PROMPT_DETAILED,
    DIFF_DETECTION_PROMPT,
    EXTRACT_TEST_PERSPECTIVES_PROMPT_WITH_DIFF,
    CREATE_TEST_SPEC_PROMPT_WITH_DIFF
)

# .envファイルから環境変数を読み込む
load_dotenv()

# --- Anthropic SDK 接続情報 ---
azure_api_key = os.getenv("AZURE_FOUNDRY_API_KEY")
azure_endpoint = os.getenv("AZURE_FOUNDRY_ENDPOINT")

# --- モデル選択 ---
model_structuring = os.getenv("MODEL_STRUCTURING")
model_test_perspectives = os.getenv("MODEL_TEST_PERSPECTIVES")
model_test_spec = os.getenv("MODEL_TEST_SPEC")
model_diff_detection = os.getenv("MODEL_DIFF_DETECTION")

# LLMクライアントの初期化用変数（遅延初期化）
anthropic_client = None

def validate_env():
    """必須環境変数のチェック関数
    
    Raises:
        ValueError: 必須環境変数が不足している場合
    """
    required = [azure_api_key, azure_endpoint, model_structuring, model_test_perspectives, model_test_spec, model_diff_detection]
    if not all(required):
        raise ValueError("Anthropic の必須環境変数が設定されていません。")

def initialize_client():
    """LLMクライアントの初期化関数
    
    初回呼び出し時のみ実行される（遅延初期化）
    """
    global anthropic_client
    validate_env()
    
    # Anthropic SDK のクライアントを初期化
    anthropic_client = AnthropicFoundry(
        api_key=azure_api_key,
        base_url=azure_endpoint
    )

def call_llm(system_prompt: str, user_prompt: str, model: str, max_retries: int = 10) -> tuple[str, dict]:
    """
    Anthropic SDK を呼び出す共通関数
    
    Args:
        system_prompt: システムプロンプト（LLMの役割や指示）
        user_prompt: ユーザープロンプト（実際の入力データ）
        model: 使用するモデル名
        max_retries: レート制限エラー時の最大リトライ回数
    
    Returns:
        tuple[str, dict]: (LLMからの応答テキスト, 使用量情報)
            使用量情報: {"input_tokens": int, "output_tokens": int, "model": str}
    
    Raises:
        RuntimeError: API呼び出しに失敗した場合
    """
    global anthropic_client
    
    # クライアントが未初期化の場合は初期化
    if anthropic_client is None:
        initialize_client()
    
    for attempt in range(max_retries):
        try:
            # Anthropic SDK を呼び出し（ストリーミング有効）
            result = ""
            input_tokens = 0
            output_tokens = 0
            
            with anthropic_client.messages.stream(
                model=model,
                max_tokens=64000,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_prompt}
                ]
            ) as stream:
                for text in stream.text_stream:
                    result += text
                
                # 最終メッセージから使用量情報を取得
                message = stream.get_final_message()
                input_tokens = message.usage.input_tokens
                output_tokens = message.usage.output_tokens
            
            usage_info = {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "model": model
            }
            return result, usage_info

        except Exception as e:
            # レート制限エラーの場合はリトライ
            if "rate_limit" in str(e).lower() or "429" in str(e):
                if attempt < max_retries - 1:
                    wait_time = min((2 ** attempt) * 3 + (attempt * 5), 120)
                    logging.warning(f"Anthropic API レート制限エラー。{wait_time}秒後にリトライします（{attempt + 1}/{max_retries}）")
                    time.sleep(wait_time)
                    continue
                else:
                    logging.error("Anthropic API呼び出しが最大リトライ回数に達しました")
                    raise RuntimeError("Anthropic APIのレート制限エラー。時間をおいて再試行してください。")
            
            # その他のエラー
            logging.error(f"Anthropic API呼び出し中にエラーが発生しました: {str(e)}")
            raise RuntimeError(f"Anthropic API呼び出しに失敗しました: {str(e)}")
    
    raise RuntimeError("Anthropic API呼び出しに失敗しました")


# --- ビジネスロジック固有のLLM呼び出し関数 ---

def structuring(prompt: str) -> tuple[str, dict]:
    """Excelシートの生データをAIで構造化されたMarkdownに変換
    
    Returns:
        tuple[str, dict]: (構造化されたMarkdown, 使用量情報)
    """
    return call_llm(STRUCTURING_PROMPT, prompt, model_structuring)

def extract_test_perspectives(prompt: str) -> tuple[str, dict]:
    """設計書からAIでテスト観点を抽出
    
    Returns:
        tuple[str, dict]: (テスト観点, 使用量情報)
    """
    return call_llm(EXTRACT_TEST_PERSPECTIVES_PROMPT, prompt, model_test_perspectives)

def create_test_spec(prompt: str, granularity: str = "simple") -> tuple[str, dict]:
    """テスト仕様書を生成
    
    Args:
        prompt: 設計書とテスト観点を含むプロンプト
        granularity: テスト粒度（"simple" or "detailed"）
    
    Returns:
        tuple[str, dict]: (テスト仕様書, 使用量情報)
    """
    system_prompt = CREATE_TEST_SPEC_PROMPT_DETAILED if granularity == "detailed" else CREATE_TEST_SPEC_PROMPT_SIMPLE
    return call_llm(system_prompt, prompt, model_test_spec)

def detect_diff(prompt: str) -> tuple[str, dict]:
    """旧版と新版の設計書から差分を検知
    
    Returns:
        tuple[str, dict]: (差分サマリー, 使用量情報)
    """
    return call_llm(DIFF_DETECTION_PROMPT, prompt, model_diff_detection)

def extract_perspectives_with_diff(prompt: str) -> tuple[str, dict]:
    """差分を考慮してテスト観点を抽出（差分モード用）
    
    Returns:
        tuple[str, dict]: (テスト観点, 使用量情報)
    """
    return call_llm(EXTRACT_TEST_PERSPECTIVES_PROMPT_WITH_DIFF, prompt, model_test_perspectives)

def create_test_spec_with_diff(prompt: str) -> tuple[str, dict]:
    """差分と旧版仕様書を考慮してテスト仕様書を生成（差分モード用）
    
    Returns:
        tuple[str, dict]: (テスト仕様書, 使用量情報)
    """
    return call_llm(CREATE_TEST_SPEC_PROMPT_WITH_DIFF, prompt, model_test_spec)
