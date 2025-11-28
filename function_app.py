# ==================== インポート ====================
import azure.functions as func  # Azure Functions基本ライブラリ
import azure.durable_functions as df  # Durable Functions（長時間処理・非同期処理用）
import logging  # ログ出力
import json  # JSON操作
import os  # 環境変数取得
from urllib.parse import quote  # URLエンコード（ファイル名用）
from azure.storage.blob import BlobServiceClient  # Azure Blob Storage操作
from azure.core.exceptions import ResourceExistsError  # コンテナ重複エラー

from core import normal_mode, diff_mode  # ビジネスロジック層

# ==================== 進捗管理の仕組み ====================
# Durable Functionsでは、Orchestrator内で外部リソース（Blob Storage）への
# 直接アクセスが禁止されているため、Activity関数内で進捗更新を行う必要がある。
# そのため、グローバル変数とコールバック関数を使って進捗更新を実現している。

_progress_callback = None  # 進捗更新用のコールバック関数（Activity内で設定）

def set_progress_callback(callback):
    """Activity関数内で進捗更新用のコールバック関数を設定
    
    Args:
        callback: 進捗更新を行う関数（stage, message, progressを引数に取る）
    """
    global _progress_callback
    _progress_callback = callback

class DurableProgressManager:
    """Durable Functions用のProgressManager
    
    coreモジュールのProgressManagerを置き換えて、
    Activity関数内で設定されたコールバック関数経由で進捗更新を行う。
    """
    def update_progress(self, job_id, stage, message, progress):
        """進捗を更新（コールバック関数経由）"""
        if _progress_callback:
            _progress_callback(stage, message, progress)
    
    def get_progress(self, job_id):
        """進捗取得（Durable Functionsでは使用しない）"""
        return None
    
    def delete_progress(self, job_id):
        """進捗削除（Durable Functionsでは使用しない）"""
        pass

# coreモジュールのProgressManagerをDurable Functions用に置き換え
try:
    import core.progress_manager
    core.progress_manager.ProgressManager = DurableProgressManager
except:
    pass

# ==================== アプリケーション初期化 ====================
app = df.DFApp(http_auth_level=func.AuthLevel.ANONYMOUS)  # Durable Functionsアプリ（認証なし）

# CORS設定（フロントエンドからのアクセスを許可）
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",  # 全オリジンを許可（本番環境では制限推奨）
    "Access-Control-Allow-Methods": "POST, GET, OPTIONS",  # 許可するHTTPメソッド
    "Access-Control-Allow-Headers": "Content-Type"  # 許可するヘッダー
}

# ==================== Blob Storage操作用ヘルパー関数 ====================

def get_blob_service_client():
    """Azure Blob Storage Clientを取得
    
    環境変数からAzure Storageの接続文字列を取得し、
    BlobServiceClientを初期化して返す。
    
    Returns:
        BlobServiceClient: Blob Storage操作用クライアント
    
    Raises:
        ValueError: 環境変数が設定されていない場合
    """
    connection_string = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if not connection_string:
        raise ValueError("AZURE_STORAGE_CONNECTION_STRING が設定されていません")
    return BlobServiceClient.from_connection_string(connection_string)

def ensure_container_exists(container_name: str):
    """Blobコンテナが存在しない場合は作成
    
    指定されたコンテナが存在しない場合は新規作成する。
    既に存在する場合はResourceExistsErrorが発生するが、無視して続行する。
    
    Args:
        container_name: 作成するコンテナ名
            - temp-uploads: アップロードされたファイルの一時保存先
            - results: 生成されたZIPファイルの保存先
            - progress: 進捗情報の保存先
    """
    try:
        blob_service_client = get_blob_service_client()
        blob_service_client.create_container(container_name)
        logging.info(f"コンテナ作成: {container_name}")
    except ResourceExistsError:
        # コンテナが既に存在する場合（正常系）
        logging.info(f"コンテナ既存: {container_name}")
    except Exception as e:
        # その他のエラー（権限不足など）
        logging.error(f"コンテナ作成エラー: {e}")

# ==================== HTTP Starters ====================
# Starter関数は、HTTPリクエストを受け付けてOrchestrator関数を起動する役割を持つ。
# HTTP応答230秒制限を回避するため、ファイルをBlobに保存してinstance_idを即座に返却する。
# 実際の処理はOrchestrator → Activity関数で非同期実行される。

@app.route(route="upload", methods=["POST", "OPTIONS"])
@app.durable_client_input(client_name="client")  # Durable Functionsクライアントを注入
async def upload_starter(req: func.HttpRequest, client) -> func.HttpResponse:
    """通常モード: 設計書をアップロードしてテスト仕様書生成を開始
    
    処理フロー:
    1. アップロードされたExcelファイルをBlob Storageに保存
    2. Orchestrator関数を起動してinstance_idを取得
    3. Blobパスをinstance_idに変更（一意性確保）
    4. Orchestratorにデータを送信
    5. instance_idを含むレスポンスを即座に返却（3~5秒）
    
    Args:
        req: HTTPリクエスト（FormDataでファイルを受信）
            - documentFiles: 設計書Excelファイル（複数可）
            - granularity: テスト粒度（"simple" or "detailed"）
        client: Durable Functionsクライアント
    
    Returns:
        HttpResponse: instance_idとステータス確認用URLを含むレスポンス
    """
    # CORS preflight対応（OPTIONSリクエスト）
    if req.method == "OPTIONS":
        return func.HttpResponse(status_code=200, headers=CORS_HEADERS)
    
    try:
        # ========== 1. リクエストデータの取得 ==========
        files = req.files.getlist("documentFiles")  # 複数ファイル対応
        if not files:
            return func.HttpResponse("ファイルがアップロードされていません", status_code=400, headers=CORS_HEADERS)
        
        granularity = req.form.get("granularity", "simple")  # テスト粒度（デフォルト: simple）
        
        # ========== 2. Orchestratorを起動してinstance_idを取得 ==========
        # instance_idはDurable Functionsが自動生成する一意のID
        instance_id = await client.start_new("orchestrator")
        
        # ========== 3. Blob Storageの準備 ==========
        ensure_container_exists("temp-uploads")
        blob_service_client = get_blob_service_client()
        file_refs = []
        
        # ========== 4. ファイルをBlobに保存（instance_idを使用） ==========
        for idx, file in enumerate(files):
            blob_name = f"{instance_id}/input/file_{idx}_{file.filename}"
            blob_client = blob_service_client.get_blob_client(container="temp-uploads", blob=blob_name)
            blob_client.upload_blob(file.stream.read(), overwrite=True)
            
            file_refs.append({
                "filename": file.filename,
                "blob_name": blob_name,
                "container": "temp-uploads"
            })
        
        # ========== 5. Orchestratorにデータを送信 ==========
        input_data = {
            "mode": "normal",  # 処理モード（通常モード）
            "files": file_refs,  # ファイル参照情報
            "granularity": granularity,  # テスト粒度
            "instance_id": instance_id  # ジョブID
        }
        
        # raise_eventでOrchestratorにデータを送信（イベント名: start_processing）
        await client.raise_event(instance_id, "start_processing", input_data)
        
        # ========== 6. レスポンスを即座に返却（3~5秒） ==========
        # create_check_status_responseは以下のURLを含むレスポンスを生成:
        # - statusQueryGetUri: 進捗確認用URL
        # - sendEventPostUri: イベント送信用URL
        # - terminatePostUri: 処理中断用URL
        response = client.create_check_status_response(req, instance_id)
        response.headers.update(CORS_HEADERS)
        return response
        
    except Exception as e:
        logging.error(f"Starter error: {e}")
        return func.HttpResponse(f"エラー: {str(e)}", status_code=500, headers=CORS_HEADERS)


@app.route(route="upload_diff", methods=["POST", "OPTIONS"])
@app.durable_client_input(client_name="client")
async def upload_diff_starter(req: func.HttpRequest, client) -> func.HttpResponse:
    """差分モード: 新旧設計書を比較してテスト仕様書の差分版を生成
    
    処理フロー:
    1. 新版設計書（Excel）と旧版成果物（MD）をBlob Storageに保存
    2. Orchestrator関数を起動してinstance_idを取得
    3. Blobパスをinstance_idに変更
    4. Orchestratorにデータを送信
    5. instance_idを含むレスポンスを即座に返却
    
    Args:
        req: HTTPリクエスト（FormDataでファイルを受信）
            - newExcelFiles: 新版設計書Excelファイル（複数可）
            - oldStructuredMd: 旧版の構造化設計書（.md）
            - oldTestSpecMd: 旧版のテスト仕様書（.md）
            - granularity: テスト粒度（"simple" or "detailed"）
        client: Durable Functionsクライアント
    
    Returns:
        HttpResponse: instance_idとステータス確認用URLを含むレスポンス
    """
    # CORS preflight対応
    if req.method == "OPTIONS":
        return func.HttpResponse(status_code=200, headers=CORS_HEADERS)
    
    try:
        # ========== 1. リクエストデータの取得 ==========
        new_excel_files = req.files.getlist("newExcelFiles")  # 新版設計書（複数可）
        old_structured_md = req.files.get("oldStructuredMd")  # 旧版の構造化設計書
        old_test_spec_md = req.files.get("oldTestSpecMd")  # 旧版のテスト仕様書
        
        # 必須ファイルのバリデーション
        if not new_excel_files or not old_structured_md or not old_test_spec_md:
            return func.HttpResponse("必要なファイルがアップロードされていません", status_code=400, headers=CORS_HEADERS)
        
        granularity = req.form.get("granularity", "simple")
        
        # Orchestratorを起動してinstance_idを取得
        instance_id = await client.start_new("orchestrator")
        
        # コンテナ作成
        ensure_container_exists("temp-uploads")
        blob_service_client = get_blob_service_client()
        file_refs = []
        
        # ファイルをBlobに保存（instance_idを使用）
        for idx, file in enumerate(new_excel_files):
            blob_name = f"{instance_id}/input/new_excel_{idx}_{file.filename}"
            blob_client = blob_service_client.get_blob_client(container="temp-uploads", blob=blob_name)
            blob_client.upload_blob(file.stream.read(), overwrite=True)
            file_refs.append({
                "filename": file.filename,
                "blob_name": blob_name,
                "container": "temp-uploads"
            })
        
        # 旧版ファイルも保存
        old_md_blob = f"{instance_id}/input/old_structured.md"
        blob_client = blob_service_client.get_blob_client(container="temp-uploads", blob=old_md_blob)
        blob_client.upload_blob(old_structured_md.stream.read(), overwrite=True)
        
        old_spec_blob = f"{instance_id}/input/old_test_spec.md"
        blob_client = blob_service_client.get_blob_client(container="temp-uploads", blob=old_spec_blob)
        blob_client.upload_blob(old_test_spec_md.stream.read(), overwrite=True)
        
        input_data = {
            "mode": "diff",
            "files": file_refs,
            "old_structured_md_blob": old_md_blob,
            "old_test_spec_md_blob": old_spec_blob,
            "granularity": granularity,
            "instance_id": instance_id
        }
        
        await client.raise_event(instance_id, "start_processing", input_data)
        
        response = client.create_check_status_response(req, instance_id)
        response.headers.update(CORS_HEADERS)
        return response
        
    except Exception as e:
        logging.error(f"Diff starter error: {e}")
        return func.HttpResponse(f"エラー: {str(e)}", status_code=500, headers=CORS_HEADERS)


# ==================== Orchestrator ====================
# Orchestrator関数は、処理全体の流れを管理する役割を持つ。
# - Starter関数からのイベントを待機
# - Activity関数を呼び出して実際の処理を実行
# - 進捗状態を保持（set_custom_status）
# - 処理結果を返却
#
# 重要な特性:
# - Orchestrator関数は「決定論的」である必要がある（同じ入力で同じ結果）
# - 外部リソース（Blob Storage、HTTP通信など）への直接アクセスは禁止
# - リプレイ機構により、障害発生時も処理を継続できる

@app.orchestration_trigger(context_name="context")
def orchestrator(context: df.DurableOrchestrationContext):
    """テスト仕様書生成処理を管理するOrchestrator
    
    処理フロー:
    1. Starter関数からのイベント（start_processing）を待機
    2. 初期進捗ステータスを設定
    3. Activity関数（process_test_generation）を呼び出し
    4. 完了ステータスを設定
    5. 結果を返却
    
    Args:
        context: Orchestratorコンテキスト
            - instance_id: ジョブID
            - is_replaying: リプレイ中かどうか
    
    Returns:
        dict: Activity関数の実行結果（Blob参照情報）
    """
    try:
        # ========== 1. Starter関数からのイベントを待機 ==========
        # wait_for_external_eventは、raise_eventで送信されたデータを受信する
        # イベント名: "start_processing"
        # 
        # リプレイ機構について:
        # Durable Functionsは、障害発生時に処理を再開するため、
        # Orchestrator関数を最初から再実行（リプレイ）する。
        # is_replayingフラグで、リプレイ中かどうかを判定できる。
        if not context.is_replaying:
            logging.info(f"Orchestrator waiting for event: {context.instance_id}")
        
        input_data = yield context.wait_for_external_event("start_processing")
        
        if not context.is_replaying:
            logging.info(f"Orchestrator received event: {context.instance_id}")
        
        # ========== 2. 初期進捗ステータスを設定 ==========
        # set_custom_statusで設定した情報は、/api/status/{instanceId}で取得可能
        context.set_custom_status({
            "stage": "structuring",  # 処理ステージ
            "progress": 10,  # 進捗率（%）
            "message": "設計書を構造化中..."  # 表示メッセージ
        })
        
        # ========== 3. Activity関数を呼び出し ==========
        # call_activityは、Activity関数を呼び出して結果を待機する
        # Activity関数は無制限に実行できる（HTTP応答230秒制限の影響を受けない）
        result = yield context.call_activity("process_test_generation", input_data)
        
        # ========== 4. 完了ステータスを設定 ==========
        context.set_custom_status({
            "stage": "completed",
            "progress": 100,
            "message": "完了しました"
        })
        
        # ========== 5. 結果を返却 ==========
        # 結果は/api/status/{instanceId}のoutputフィールドで取得可能
        return result
        
    except Exception as e:
        # エラー発生時は失敗ステータスを設定
        logging.error(f"Orchestrator error: {e}")
        context.set_custom_status({
            "stage": "failed",
            "progress": 0,
            "message": f"エラー: {str(e)}"
        })
        raise


# ==================== Activity Functions ====================
# Activity関数は、実際のビジネスロジックを実行する役割を持つ。
# - Orchestrator関数から呼び出される
# - 無制限に実行できる（HTTP応答230秒制限の影響を受けない）
# - 外部リソース（Blob Storage、LLM API）へのアクセスが可能
# - 進捗情報をBlob Storageに保存してフロントエンドに通知

@app.activity_trigger(input_name="inputData")
def process_test_generation(inputData) -> dict:
    """テスト仕様書生成の実処理を実行するActivity関数
    
    処理フロー:
    1. Orchestratorから受け取ったデータを解析
    2. Blob Storageからファイルを取得
    3. 進捗更新用のコールバック関数を設定
    4. coreモジュールを呼び出してテスト仕様書を生成
    5. 生成結果をBlob Storageに保存
    6. Blob参照情報を返却
    
    Args:
        inputData: Orchestratorから渡されたデータ
            - mode: 処理モード（"normal" or "diff"）
            - files: ファイル参照情報のリスト
            - granularity: テスト粒度（"simple" or "detailed"）
            - instance_id: ジョブID
            - old_structured_md_blob: 旧版構造化設計書のBlobパス（差分モードのみ）
            - old_test_spec_md_blob: 旧版テスト仕様書のBlobパス（差分モードのみ）
    
    Returns:
        dict: 生成結果のBlob参照情報
            - blob_name: Blob Storage上のパス
            - filename: ファイル名
            - container: コンテナ名
    """
    import io
    from datetime import datetime
    
    # ========== 1. 入力データの解析 ==========
    # Durable FunctionsはJSON文字列として渡す場合があるため、パース処理を追加
    if isinstance(inputData, str):
        inputData = json.loads(inputData)
    
    mode = inputData["mode"]  # 処理モード（normal or diff）
    granularity = inputData["granularity"]  # テスト粒度（simple or detailed）
    instance_id = inputData["instance_id"]  # ジョブID（進捗管理・結果保存に使用）
    
    # ========== 2. 進捗更新用のヘルパー関数を定義 ==========
    # Orchestrator内ではBlob Storageへの直接アクセスが禁止されているため、
    # Activity関数内で進捗情報をBlob Storageに保存する。
    # この関数は、coreモジュールのProgressManagerから呼び出される。
    def update_progress_direct(stage, message, progress):
        """進捗情報をBlob Storage（progressコンテナ）に保存
        
        Args:
            stage: 処理ステージ（structuring, perspectives, testspec, converting, completed）
            message: 表示メッセージ
            progress: 進捗率（0-100）
        """
        try:
            blob_service_client = get_blob_service_client()
            ensure_container_exists("progress")  # progressコンテナを作成
            
            # 進捗情報をJSON形式で保存（ファイル名: {instance_id}.json）
            blob_client = blob_service_client.get_blob_client("progress", f"{instance_id}.json")
            data = {
                "stage": stage,
                "message": message,
                "progress": progress,
                "timestamp": datetime.utcnow().isoformat()  # UTC時刻
            }
            blob_client.upload_blob(json.dumps(data, ensure_ascii=False), overwrite=True)
            logging.info(f"進捗更新: {stage} ({progress}%)")
        except Exception as e:
            # 進捗更新失敗は処理を中断しない（ログのみ）
            logging.error(f"進捗更新失敗: {e}")
    
    # ========== 3. coreモジュールにコールバック関数を設定 ==========
    # coreモジュールのProgressManagerが、このコールバック関数を呼び出して進捗を更新する
    set_progress_callback(update_progress_direct)
    
    blob_service_client = get_blob_service_client()
    
    # ========== 4. ファイルラッパークラスの定義 ==========
    # Blob Storageから取得したバイナリデータを、coreモジュールが期待する
    # ファイルオブジェクト形式にラップする。
    class FileWrapper:
        """Blob Storageから取得したデータをファイルオブジェクトとして扱うラッパー
        
        coreモジュールは、HTTPリクエストから受け取ったファイルオブジェクトを想定しているため、
        同じインターフェース（filename, stream, read）を提供する。
        """
        def __init__(self, filename, content):
            self.filename = filename  # 元のファイル名
            self.stream = io.BytesIO(content)  # バイナリデータをストリームに変換
        
        def read(self):
            """core/utils.pyとの互換性のためのreadメソッド
            
            Returns:
                bytes: ファイルの内容
            """
            self.stream.seek(0)  # ストリームの先頭に戻す
            return self.stream.read()
    
    try:
        # ========== 5. 処理モードに応じた実行 ==========
        if mode == "normal":
            # ========== 通常モード: 設計書からテスト仕様書を生成 ==========
            
            # 5-1. Blob Storageからファイルを取得
            files = []
            for file_ref in inputData["files"]:
                # Blob参照情報からファイルを取得
                blob_client = blob_service_client.get_blob_client(
                    container=file_ref["container"],  # temp-uploads
                    blob=file_ref["blob_name"]  # {instance_id}/input/file_0_xxx.xlsx
                )
                content = blob_client.download_blob().readall()  # バイナリデータを取得
                files.append(FileWrapper(file_ref["filename"], content))
            
            # 5-2. coreモジュールを呼び出してテスト仕様書を生成
            # normal_mode.generate_normal_test_spec()は以下を実行:
            # - Excel解析 → Markdown構造化
            # - LLMでテスト観点抽出
            # - LLMでテスト仕様書生成
            # - Excel/CSV変換
            # - ZIPファイル作成
            zip_bytes = normal_mode.generate_normal_test_spec(files, granularity, instance_id)
            filename = "テスト仕様書.zip"
            
        else:
            # ========== 差分モード: 新旧設計書を比較してテスト仕様書の差分版を生成 ==========
            
            # 5-1. 新版設計書をBlob Storageから取得
            files = []
            for file_ref in inputData["files"]:
                blob_client = blob_service_client.get_blob_client(
                    container=file_ref["container"],
                    blob=file_ref["blob_name"]
                )
                content = blob_client.download_blob().readall()
                files.append(FileWrapper(file_ref["filename"], content))
            
            # 5-2. 旧版の構造化設計書を取得
            blob_client = blob_service_client.get_blob_client(
                container="temp-uploads",
                blob=inputData["old_structured_md_blob"]  # {instance_id}/input/old_structured.md
            )
            old_structured_content = blob_client.download_blob().readall()
            old_structured_md = FileWrapper("old_structured.md", old_structured_content)
            
            # 5-3. 旧版のテスト仕様書を取得
            blob_client = blob_service_client.get_blob_client(
                container="temp-uploads",
                blob=inputData["old_test_spec_md_blob"]  # {instance_id}/input/old_test_spec.md
            )
            old_spec_content = blob_client.download_blob().readall()
            old_test_spec_md = FileWrapper("old_test_spec.md", old_spec_content)
            
            # 5-4. coreモジュールを呼び出して差分版テスト仕様書を生成
            # diff_mode.generate_diff_test_spec()は以下を実行:
            # - 新版Excel解析 → Markdown構造化
            # - LLMで差分検知
            # - LLMで差分考慮のテスト観点抽出
            # - LLMで差分版テスト仕様書生成
            # - Excel/CSV変換
            # - ZIPファイル作成
            zip_bytes = diff_mode.generate_diff_test_spec(
                files, old_structured_md, old_test_spec_md, granularity, instance_id
            )
            filename = "テスト仕様書_差分版.zip"
        
        # ========== 6. 生成結果をBlob Storageに保存 ==========
        ensure_container_exists("results")  # resultsコンテナを作成
        
        # Blobパス: {instance_id}/{filename}
        # 例: abc123-def456/テスト仕様書.zip
        blob_name = f"{instance_id}/{filename}"
        blob_client = blob_service_client.get_blob_client(container="results", blob=blob_name)
        blob_client.upload_blob(zip_bytes, overwrite=True)
        
        logging.info(f"結果保存完了: {blob_name}")
        
        # ========== 7. Orchestratorに結果を返却 ==========
        # この情報は、Orchestratorの戻り値として/api/status/{instanceId}で取得可能
        return {
            "blob_name": blob_name,  # Blob Storage上のパス
            "filename": filename,  # ファイル名
            "container": "results"  # コンテナ名
        }
        
    except Exception as e:
        logging.error(f"Activity error: {e}")
        raise


# ==================== Status ====================
# Status関数は、ジョブの進捗状況を取得するエンドポイント。
# フロントエンドが10秒間隔でポーリングして進捗を確認する。

@app.route(route="status/{instanceId}", methods=["GET", "OPTIONS"])
@app.durable_client_input(client_name="client")
async def get_status(req: func.HttpRequest, client) -> func.HttpResponse:
    """ジョブの進捗状況を取得
    
    処理フロー:
    1. Orchestratorのステータスを取得（runtimeStatus）
    2. Blob Storageから詳細な進捗情報を取得（customStatus）
    3. JSON形式でレスポンスを返却
    
    Args:
        req: HTTPリクエスト
            - instanceId: ジョブID（URLパラメータ）
        client: Durable Functionsクライアント
    
    Returns:
        HttpResponse: 進捗情報を含むJSONレスポンス
            - instanceId: ジョブID
            - runtimeStatus: 実行状態（Running, Completed, Failed等）
            - customStatus: 詳細な進捗情報（stage, message, progress）
            - createdTime: ジョブ開始時刻
            - lastUpdatedTime: 最終更新時刻
            - output: 処理結果（Completed時のみ）
    """
    # CORS preflight対応
    if req.method == "OPTIONS":
        return func.HttpResponse(status_code=200, headers=CORS_HEADERS)
    
    # URLパラメータからinstance_idを取得
    instance_id = req.route_params.get("instanceId")
    
    try:
        # ========== 1. Orchestratorのステータスを取得 ==========
        # get_statusは、Durable Functionsの管理情報からジョブの状態を取得
        status = await client.get_status(instance_id)
        
        # ジョブが見つからない場合（無効なinstance_id）
        if not status:
            return func.HttpResponse(
                json.dumps({"error": "ジョブが見つかりません"}, ensure_ascii=False),
                mimetype="application/json",
                status_code=404,
                headers=CORS_HEADERS
            )
        
        # ========== 2. Blob Storageから詳細な進捗情報を取得 ==========
        # Orchestratorのset_custom_statusで設定した情報は粗い粒度のため、
        # Activity関数がBlob Storageに保存した詳細な進捗情報を優先的に使用する。
        custom_status = status.custom_status  # デフォルト値（Orchestratorの情報）
        try:
            blob_service_client = get_blob_service_client()
            blob_client = blob_service_client.get_blob_client("progress", f"{instance_id}.json")
            progress_data = blob_client.download_blob().readall()
            custom_status = json.loads(progress_data)  # Activity関数が保存した詳細情報
        except:
            # progressデータがない場合（処理開始直後など）は、Orchestratorの情報を使用
            pass
        
        # ========== 3. レスポンスデータを組み立て ==========
        response_data = {
            "instanceId": instance_id,
            "runtimeStatus": status.runtime_status.name,  # Running, Completed, Failed等
            "customStatus": custom_status,  # 詳細な進捗情報
            "createdTime": status.created_time.isoformat() if status.created_time else None,
            "lastUpdatedTime": status.last_updated_time.isoformat() if status.last_updated_time else None
        }
        
        # 処理完了時は、Activity関数の戻り値（Blob参照情報）を含める
        if status.runtime_status == df.OrchestrationRuntimeStatus.Completed:
            response_data["output"] = status.output
        
        # ========== 4. JSON形式でレスポンスを返却 ==========
        return func.HttpResponse(
            json.dumps(response_data, ensure_ascii=False),
            mimetype="application/json",
            status_code=200,
            headers=CORS_HEADERS
        )
        
    except Exception as e:
        logging.error(f"Status query error: {e}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}, ensure_ascii=False),
            mimetype="application/json",
            status_code=500,
            headers=CORS_HEADERS
        )

# ==================== List Results ====================
@app.route(route="list-results", methods=["GET", "OPTIONS"])
async def list_results(req: func.HttpRequest) -> func.HttpResponse:
    """過去の処理結果一覧を取得"""
    if req.method == "OPTIONS":
        return func.HttpResponse(status_code=200, headers=CORS_HEADERS)
    
    try:
        blob_service_client = get_blob_service_client()
        ensure_container_exists("results")
        ensure_container_exists("progress")
        results_container = blob_service_client.get_container_client("results")
        progress_container = blob_service_client.get_container_client("progress")
        
        results = []
        seen_ids = set()
        
        for blob in results_container.list_blobs():
            instance_id = blob.name.split("/")[0]
            if instance_id in seen_ids:
                continue
            seen_ids.add(instance_id)
            
            # 進捗情報から詳細を取得
            try:
                progress_blob = progress_container.get_blob_client(f"{instance_id}.json")
                progress_data = json.loads(progress_blob.download_blob().readall())
                timestamp = progress_data.get("timestamp", "")
            except:
                timestamp = blob.last_modified.isoformat() if blob.last_modified else ""
            
            results.append({
                "instanceId": instance_id,
                "filename": blob.name.split("/")[-1],
                "timestamp": timestamp,
                "size": blob.size
            })
        
        results.sort(key=lambda x: x["timestamp"], reverse=True)
        
        return func.HttpResponse(
            json.dumps(results, ensure_ascii=False),
            mimetype="application/json",
            status_code=200,
            headers=CORS_HEADERS
        )
    except Exception as e:
        logging.error(f"List results error: {e}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}, ensure_ascii=False),
            mimetype="application/json",
            status_code=500,
            headers=CORS_HEADERS
        )

# ==================== Download ====================
# Download関数は、生成されたZIPファイルをダウンロードするエンドポイント。
# フロントエンドは、ジョブ完了後にこのエンドポイントを呼び出してファイルを取得する。

@app.route(route="download/{instanceId}", methods=["GET", "OPTIONS"])
async def download_result(req: func.HttpRequest) -> func.HttpResponse:
    """生成されたZIPファイルをダウンロード
    
    処理フロー:
    1. Blob Storage（resultsコンテナ）からZIPファイルを取得
    2. Content-Dispositionヘッダーでファイル名を指定
    3. バイナリデータをレスポンスとして返却
    
    Args:
        req: HTTPリクエスト
            - instanceId: ジョブID（URLパラメータ）
    
    Returns:
        HttpResponse: ZIPファイルのバイナリデータ
    """
    # CORS preflight対応
    if req.method == "OPTIONS":
        return func.HttpResponse(status_code=200, headers=CORS_HEADERS)
    
    # URLパラメータからinstance_idを取得
    instance_id = req.route_params.get("instanceId")
    
    try:
        # ========== 1. Blob Storageからファイルを検索 ==========
        blob_service_client = get_blob_service_client()
        container_client = blob_service_client.get_container_client("results")
        
        # instance_idで始まるBlobを検索
        # 例: abc123-def456/テスト仕様書.zip
        blobs = list(container_client.list_blobs(name_starts_with=f"{instance_id}/"))
        
        # ファイルが見つからない場合（処理未完了 or エラー）
        if not blobs:
            return func.HttpResponse("ファイルが見つかりません", status_code=404, headers=CORS_HEADERS)
        
        # ========== 2. ZIPファイルをダウンロード ==========
        # 最初に見つかったBlobを取得（通常は1つのみ）
        blob_name = blobs[0].name
        blob_client = blob_service_client.get_blob_client(container="results", blob=blob_name)
        
        # バイナリデータを取得
        zip_bytes = blob_client.download_blob().readall()
        
        # ========== 3. ファイル名を抽出してエンコード ==========
        # Blobパス: {instance_id}/{filename} から filename を抽出
        filename = blob_name.split("/")[-1]  # 例: テスト仕様書.zip
        encoded_filename = quote(filename)  # URLエンコード（日本語対応）
        
        # ========== 4. レスポンスヘッダーを設定 ==========
        headers = {
            # Content-Disposition: ブラウザにダウンロードを指示
            # filename*=UTF-8'': RFC 5987形式（日本語ファイル名対応）
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
            "Content-Type": "application/zip",  # ZIPファイルのMIMEタイプ
            **CORS_HEADERS  # CORS設定を追加
        }
        
        # ========== 5. バイナリデータをレスポンスとして返却 ==========
        return func.HttpResponse(zip_bytes, status_code=200, headers=headers)
        
    except Exception as e:
        # ダウンロードエラー（Blob Storage接続エラー、権限不足など）
        logging.error(f"Download error: {e}")
        return func.HttpResponse(f"エラー: {str(e)}", status_code=500, headers=CORS_HEADERS)

# ==================== Delete Result ====================
@app.route(route="delete/{instanceId}", methods=["DELETE", "OPTIONS"])
async def delete_result(req: func.HttpRequest) -> func.HttpResponse:
    """処理結果を削除"""
    if req.method == "OPTIONS":
        return func.HttpResponse(status_code=200, headers=CORS_HEADERS)
    
    instance_id = req.route_params.get("instanceId")
    
    try:
        blob_service_client = get_blob_service_client()
        
        # resultsコンテナから削除
        container_client = blob_service_client.get_container_client("results")
        blobs = list(container_client.list_blobs(name_starts_with=f"{instance_id}/"))
        for blob in blobs:
            container_client.delete_blob(blob.name)
        
        # progressコンテナから削除
        try:
            progress_client = blob_service_client.get_blob_client("progress", f"{instance_id}.json")
            progress_client.delete_blob()
        except:
            pass
        
        return func.HttpResponse(
            json.dumps({"message": "削除しました"}, ensure_ascii=False),
            mimetype="application/json",
            status_code=200,
            headers=CORS_HEADERS
        )
    except Exception as e:
        logging.error(f"Delete error: {e}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}, ensure_ascii=False),
            mimetype="application/json",
            status_code=500,
            headers=CORS_HEADERS
        )
