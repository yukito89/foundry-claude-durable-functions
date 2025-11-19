import logging
import os
import time
import json
from openai import AzureOpenAI
import boto3
from botocore.config import Config
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

# --- LLMサービス設定 ---
llm_service = os.getenv("LLM_SERVICE", "AWS")  # 使用するLLMサービス（"AWS" or "AZURE"）

# --- Azure OpenAI Service 接続情報 ---
azure_api_key = os.getenv("AZURE_OPENAI_API_KEY")
azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
azure_api_version = os.getenv("AZURE_OPENAI_API_VERSION")
azure_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")

# --- AWS Bedrock 接続情報 ---
aws_region = os.getenv("AWS_REGION")
aws_access_key_id = os.getenv("AWS_ACCESS_KEY_ID")
aws_secret_access_key = os.getenv("AWS_SECRET_ACCESS_KEY")
aws_bedrock_model_id = os.getenv("AWS_BEDROCK_MODEL_ID")

# LLMクライアントの初期化用変数（遅延初期化）
azure_client = None
bedrock_client = None

def validate_env():
    """必須環境変数のチェック関数
    
    選択されたLLMサービスに応じて必要な環境変数が設定されているか検証する
    
    Raises:
        ValueError: 必須環境変数が不足している場合
    """
    if llm_service == "AZURE":
        required = [azure_api_key, azure_endpoint, azure_api_version, azure_deployment]
        if not all(required):
            raise ValueError("Azure OpenAI の必須環境変数が設定されていません。")
    elif llm_service == "AWS":
        required = [aws_region, aws_access_key_id, aws_secret_access_key, aws_bedrock_model_id]
        if not all(required):
            raise ValueError("AWS Bedrock の必須環境変数が設定されていません。")
    else:
        raise ValueError(f"無効なLLMサービスが指定されました: {llm_service}")

def initialize_client():
    """LLMクライアントの初期化関数
    
    選択されたLLMサービスに応じてクライアントを初期化する
    初回呼び出し時のみ実行される（遅延初期化）
    """
    global azure_client, bedrock_client
    validate_env()

    if llm_service == "AZURE":
        # Azure OpenAI Serviceのクライアントを初期化
        azure_client = AzureOpenAI(
            api_version=azure_api_version,
            azure_endpoint=azure_endpoint,
            api_key=azure_api_key,
        )
    elif llm_service == "AWS":
        # AWS Bedrockのクライアントを初期化（タイムアウトを延長）
        config = Config(read_timeout=900, connect_timeout=60)
        bedrock_client = boto3.client(
            "bedrock-runtime",
            region_name=aws_region,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            config=config,
        )

def call_llm(system_prompt: str, user_prompt: str, max_retries: int = 10) -> str:
    """
    指定されたLLMサービスを呼び出す共通関数
    
    Args:
        system_prompt: システムプロンプト（LLMの役割や指示）
        user_prompt: ユーザープロンプト（実際の入力データ）
        max_retries: レート制限エラー時の最大リトライ回数
    
    Returns:
        str: LLMからの応答テキスト
    
    Raises:
        RuntimeError: API呼び出しに失敗した場合
    """
    global azure_client, bedrock_client
    
    # クライアントが未初期化の場合は初期化
    if (llm_service == "AZURE" and azure_client is None) or \
       (llm_service == "AWS" and bedrock_client is None):
        initialize_client()
    
    for attempt in range(max_retries):
        try:
            if llm_service == "AZURE":
                # Azure OpenAI Serviceを呼び出し
                response = azure_client.chat.completions.create(
                    model=azure_deployment,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    max_tokens=32768,
                )
                return response.choices[0].message.content

            elif llm_service == "AWS":
                # AWS Bedrockを呼び出し
                response = bedrock_client.converse(
                    modelId=aws_bedrock_model_id,
                    messages=[{"role": "user", "content": [{"text": user_prompt}]}],
                    system=[{"text": system_prompt}],
                    inferenceConfig={"maxTokens": 64000},
                )
                # レスポンスから応答テキストを抽出
                if 'output' in response and 'message' in response['output']:
                    return response['output']['message']['content'][0]['text']
                else:
                    logging.error(f"予期しないレスポンス構造: {json.dumps(response, ensure_ascii=False)}")
                    raise RuntimeError("AWS Bedrockからの応答形式が不正です。")

        except Exception as e:
            error_message = str(e)
            # レート制限エラーの場合はリトライ
            if "ThrottlingException" in error_message or "Too many requests" in error_message:
                if attempt < max_retries - 1:
                    # 指数バックオフでリトライ間隔を計算（最大120秒）
                    wait_time = min((2 ** attempt) * 3 + (attempt * 5), 120)
                    logging.warning(f"{llm_service} API レート制限エラー。{wait_time}秒後にリトライします（{attempt + 1}/{max_retries}）")
                    time.sleep(wait_time)
                    continue
                else:
                    logging.error(f"{llm_service} API呼び出しが最大リトライ回数に達しました")
                    raise RuntimeError(f"{llm_service} APIのレート制限エラー。")
            else:
                # その他のエラーは即座に例外を発生
                logging.error(f"{llm_service} API呼び出し中にエラーが発生しました: {error_message}")
                raise RuntimeError(f"{llm_service} API呼び出しに失敗しました: {error_message}")
    
    raise RuntimeError(f"{llm_service} API呼び出しに失敗しました")


# --- ビジネスロジック固有のLLM呼び出し関数 ---

def structuring(prompt: str) -> str:
    """Excelシートの生データをAIで構造化されたMarkdownに変換"""
    return call_llm(STRUCTURING_PROMPT, prompt)

def extract_test_perspectives(prompt: str) -> str:
    """設計書からAIでテスト観点を抽出"""
    return call_llm(EXTRACT_TEST_PERSPECTIVES_PROMPT, prompt)

def create_test_spec(prompt: str, granularity: str = "simple") -> str:
    """テスト仕様書を生成
    
    Args:
        prompt: 設計書とテスト観点を含むプロンプト
        granularity: テスト粒度（"simple" or "detailed"）
    """
    system_prompt = CREATE_TEST_SPEC_PROMPT_DETAILED if granularity == "detailed" else CREATE_TEST_SPEC_PROMPT_SIMPLE
    return call_llm(system_prompt, prompt)

def detect_diff(prompt: str) -> str:
    """旧版と新版の設計書から差分を検知"""
    return call_llm(DIFF_DETECTION_PROMPT, prompt)

def extract_perspectives_with_diff(prompt: str) -> str:
    """差分を考慮してテスト観点を抽出（差分モード用）"""
    return call_llm(EXTRACT_TEST_PERSPECTIVES_PROMPT_WITH_DIFF, prompt)

def create_test_spec_with_diff(prompt: str) -> str:
    """差分と旧版仕様書を考慮してテスト仕様書を生成（差分モード用）"""
    return call_llm(CREATE_TEST_SPEC_PROMPT_WITH_DIFF, prompt)
