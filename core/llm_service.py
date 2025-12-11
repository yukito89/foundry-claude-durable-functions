import logging
import os
import time
from openai import AzureOpenAI, RateLimitError, APIError
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

# --- Azure OpenAI SDK 接続情報 ---
azure_api_key = os.getenv("AZURE_OPENAI_API_KEY")
azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
azure_api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")

# --- モデル選択 ---
model_structuring = os.getenv("MODEL_STRUCTURING")
model_test_perspectives = os.getenv("MODEL_TEST_PERSPECTIVES")
model_test_spec = os.getenv("MODEL_TEST_SPEC")
model_diff_detection = os.getenv("MODEL_DIFF_DETECTION")

# LLMクライアントの初期化用変数（遅延初期化）
openai_client = None

def validate_env():
    """必須環境変数のチェック関数
    
    Raises:
        ValueError: 必須環境変数が不足している場合
    """
    required = [azure_api_key, azure_endpoint, model_structuring, model_test_perspectives, model_test_spec, model_diff_detection]
    if not all(required):
        raise ValueError("Azure OpenAI の必須環境変数が設定されていません。")

def initialize_client():
    """LLMクライアントの初期化関数
    
    初回呼び出し時のみ実行される（遅延初期化）
    """
    global openai_client
    validate_env()
    
    # Azure OpenAI SDK のクライアントを初期化
    openai_client = AzureOpenAI(
        api_key=azure_api_key,
        azure_endpoint=azure_endpoint,
        api_version=azure_api_version
    )

def call_llm(system_prompt: str, user_prompt: str, model: str, max_retries: int = 10) -> tuple[str, dict]:
    """
    Azure OpenAI SDK を呼び出す共通関数
    
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
    global openai_client
    
    # クライアントが未初期化の場合は初期化
    if openai_client is None:
        initialize_client()
    
    for attempt in range(max_retries):
        try:
            # Azure OpenAI SDK を呼び出し（ストリーミング有効）
            result = ""
            response = openai_client.chat.completions.create(
                stream=True,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                max_completion_tokens=128000,
                model=model
            )
            
            for update in response:
                if update.choices:
                    result += update.choices[0].delta.content or ""
            
            # 使用量情報を取得（ストリーミング時は概算）
            usage_info = {
                "input_tokens": len(system_prompt + user_prompt) // 4,
                "output_tokens": len(result) // 4,
                "model": model
            }
            return result, usage_info

        except RateLimitError as e:
            # Azure OpenAI SDKのレート制限エラー
            if attempt < max_retries - 1:
                wait_time = min((2 ** attempt) * 3 + (attempt * 5), 120)
                logging.warning(f"Azure OpenAI API レート制限エラー。{wait_time}秒後にリトライします（{attempt + 1}/{max_retries}）")
                time.sleep(wait_time)
                continue
            else:
                logging.error("Azure OpenAI API呼び出しが最大リトライ回数に達しました")
                raise RuntimeError("Azure OpenAI APIのレート制限エラー。時間をおいて再試行してください。")
        
        except APIError as e:
            # Azure OpenAI SDKのAPIエラー（レート制限以外）
            logging.error(f"Azure OpenAI API呼び出し中にエラーが発生しました: {str(e)}")
            raise RuntimeError(f"Azure OpenAI API呼び出しに失敗しました: {str(e)}")
        
        except Exception as e:
            # その他の予期しないエラー
            logging.error(f"予期しないエラーが発生しました: {str(e)}")
            raise RuntimeError(f"LLM呼び出し中に予期しないエラーが発生しました: {str(e)}")
    
    raise RuntimeError("Azure OpenAI API呼び出しに失敗しました")


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

def detect_diff(prompt: str) -> str:
    """旧版と新版の設計書から差分を検知"""
    result, _ = call_llm(DIFF_DETECTION_PROMPT, prompt, model_diff_detection)
    return result

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
