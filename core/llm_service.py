import logging
import os
import time
from anthropic import AnthropicFoundry, RateLimitError, APIError
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

# --- Azure AI Foundry (Claude) 接続情報 ---
foundry_api_key = os.getenv("AZURE_FOUNDRY_API_KEY")
foundry_endpoint = os.getenv("AZURE_FOUNDRY_ENDPOINT")

# --- モデル選択 ---
model_structuring = os.getenv("MODEL_STRUCTURING")
model_test_perspectives = os.getenv("MODEL_TEST_PERSPECTIVES")
model_test_spec = os.getenv("MODEL_TEST_SPEC")
model_diff_detection = os.getenv("MODEL_DIFF_DETECTION")

# LLMクライアントの初期化用変数（遅延初期化）
foundry_client = None

def validate_env():
    """必須環境変数のチェック関数
    
    Raises:
        ValueError: 必須環境変数が不足している場合
    """
    required = [foundry_api_key, foundry_endpoint, model_structuring, model_test_perspectives, model_test_spec, model_diff_detection]
    if not all(required):
        raise ValueError("Azure AI Foundry の必須環境変数が設定されていません。")

def initialize_client():
    """LLMクライアントの初期化関数
    
    初回呼び出し時のみ実行される（遅延初期化）
    """
    global foundry_client
    validate_env()
    
    # Azure AI Foundry (Claude) のクライアントを初期化
    foundry_client = AnthropicFoundry(
        api_key=foundry_api_key,
        base_url=foundry_endpoint
    )

def call_llm(system_prompt: str, user_prompt: str, model: str, max_retries: int = 10) -> str:
    """
    Azure AI Foundry (Claude) を呼び出す共通関数
    
    Args:
        system_prompt: システムプロンプト（LLMの役割や指示）
        user_prompt: ユーザープロンプト（実際の入力データ）
        model: 使用するモデル名
        max_retries: レート制限エラー時の最大リトライ回数
    
    Returns:
        str: LLMからの応答テキスト
    
    Raises:
        RuntimeError: API呼び出しに失敗した場合
    """
    global foundry_client
    
    # クライアントが未初期化の場合は初期化
    if foundry_client is None:
        initialize_client()
    
    for attempt in range(max_retries):
        try:
            # Azure AI Foundry (Claude) を呼び出し（ストリーミング有効）
            result = ""
            with foundry_client.messages.stream(
                model=model,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                max_tokens=64000,
            ) as stream:
                for text in stream.text_stream:
                    result += text
            return result

        except RateLimitError as e:
            # Anthropic SDKのレート制限エラー
            if attempt < max_retries - 1:
                wait_time = min((2 ** attempt) * 3 + (attempt * 5), 120)
                logging.warning(f"Anthropic API レート制限エラー。{wait_time}秒後にリトライします（{attempt + 1}/{max_retries}）")
                time.sleep(wait_time)
                continue
            else:
                logging.error("Anthropic API呼び出しが最大リトライ回数に達しました")
                raise RuntimeError("Anthropic APIのレート制限エラー。時間をおいて再試行してください。")
        
        except APIError as e:
            # Anthropic SDKのAPIエラー（レート制限以外）
            logging.error(f"Anthropic API呼び出し中にエラーが発生しました: {str(e)}")
            raise RuntimeError(f"Anthropic API呼び出しに失敗しました: {str(e)}")
        
        except Exception as e:
            # その他の予期しないエラー
            logging.error(f"予期しないエラーが発生しました: {str(e)}")
            raise RuntimeError(f"LLM呼び出し中に予期しないエラーが発生しました: {str(e)}")
    
    raise RuntimeError("Azure AI Foundry API呼び出しに失敗しました")


# --- ビジネスロジック固有のLLM呼び出し関数 ---

def structuring(prompt: str) -> str:
    """Excelシートの生データをAIで構造化されたMarkdownに変換"""
    return call_llm(STRUCTURING_PROMPT, prompt, model_structuring)

def extract_test_perspectives(prompt: str) -> str:
    """設計書からAIでテスト観点を抽出"""
    return call_llm(EXTRACT_TEST_PERSPECTIVES_PROMPT, prompt, model_test_perspectives)

def create_test_spec(prompt: str, granularity: str = "simple") -> str:
    """テスト仕様書を生成
    
    Args:
        prompt: 設計書とテスト観点を含むプロンプト
        granularity: テスト粒度（"simple" or "detailed"）
    """
    system_prompt = CREATE_TEST_SPEC_PROMPT_DETAILED if granularity == "detailed" else CREATE_TEST_SPEC_PROMPT_SIMPLE
    return call_llm(system_prompt, prompt, model_test_spec)

def detect_diff(prompt: str) -> str:
    """旧版と新版の設計書から差分を検知"""
    return call_llm(DIFF_DETECTION_PROMPT, prompt, model_diff_detection)

def extract_perspectives_with_diff(prompt: str) -> str:
    """差分を考慮してテスト観点を抽出（差分モード用）"""
    return call_llm(EXTRACT_TEST_PERSPECTIVES_PROMPT_WITH_DIFF, prompt, model_test_perspectives)

def create_test_spec_with_diff(prompt: str) -> str:
    """差分と旧版仕様書を考慮してテスト仕様書を生成（差分モード用）"""
    return call_llm(CREATE_TEST_SPEC_PROMPT_WITH_DIFF, prompt, model_test_spec)
