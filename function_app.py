import azure.functions as func
import azure.durable_functions as df
import logging
import json
import os
from urllib.parse import quote
from azure.storage.blob import BlobServiceClient
from azure.core.exceptions import ResourceExistsError

from core import normal_mode, diff_mode

# 進捗更新用のグローバル関数（Activity内で使用）
_progress_callback = None

def set_progress_callback(callback):
    global _progress_callback
    _progress_callback = callback

# Durable Functions用のProgressManager
class DurableProgressManager:
    def update_progress(self, job_id, stage, message, progress):
        if _progress_callback:
            _progress_callback(stage, message, progress)
    
    def get_progress(self, job_id):
        return None
    
    def delete_progress(self, job_id):
        pass

# coreモジュールのProgressManagerを置き換え
try:
    import core.progress_manager
    core.progress_manager.ProgressManager = DurableProgressManager
except:
    pass

app = df.DFApp(http_auth_level=func.AuthLevel.ANONYMOUS)

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type"
}

def get_blob_service_client():
    """Blob Service Clientを取得"""
    connection_string = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if not connection_string:
        raise ValueError("AZURE_STORAGE_CONNECTION_STRING が設定されていません")
    return BlobServiceClient.from_connection_string(connection_string)

def ensure_container_exists(container_name: str):
    """コンテナが存在しない場合は作成"""
    try:
        blob_service_client = get_blob_service_client()
        blob_service_client.create_container(container_name)
        logging.info(f"コンテナ作成: {container_name}")
    except ResourceExistsError:
        logging.info(f"コンテナ既存: {container_name}")
    except Exception as e:
        logging.error(f"コンテナ作成エラー: {e}")

# ==================== HTTP Starters ====================

@app.route(route="upload", methods=["POST", "OPTIONS"])
@app.durable_client_input(client_name="client")
async def upload_starter(req: func.HttpRequest, client) -> func.HttpResponse:
    """通常モード: ファイルをBlobに保存してOrchestrator起動"""
    if req.method == "OPTIONS":
        return func.HttpResponse(status_code=200, headers=CORS_HEADERS)
    
    try:
        files = req.files.getlist("documentFiles")
        if not files:
            return func.HttpResponse("ファイルがアップロードされていません", status_code=400, headers=CORS_HEADERS)
        
        granularity = req.form.get("granularity", "simple")
        
        # コンテナ作成
        ensure_container_exists("temp-uploads")
        
        # 一時的なinstance_idを生成（Blobパス用）
        import uuid
        temp_id = str(uuid.uuid4())
        
        # ファイルをBlobに保存
        blob_service_client = get_blob_service_client()
        file_refs = []
        
        for idx, file in enumerate(files):
            blob_name = f"{temp_id}/input/file_{idx}_{file.filename}"
            blob_client = blob_service_client.get_blob_client(container="temp-uploads", blob=blob_name)
            blob_client.upload_blob(file.stream.read(), overwrite=True)
            file_refs.append({
                "filename": file.filename,
                "blob_name": blob_name,
                "container": "temp-uploads"
            })
        
        # Orchestratorを起動（instance_idを先に取得）
        instance_id = await client.start_new("orchestrator")
        
        # instance_idを使ってBlobパスを更新
        for file_ref in file_refs:
            old_blob_name = file_ref["blob_name"]
            new_blob_name = old_blob_name.replace(temp_id, instance_id)
            
            # Blobをコピー
            old_blob_client = blob_service_client.get_blob_client(container="temp-uploads", blob=old_blob_name)
            new_blob_client = blob_service_client.get_blob_client(container="temp-uploads", blob=new_blob_name)
            new_blob_client.start_copy_from_url(old_blob_client.url)
            
            # 参照を更新
            file_ref["blob_name"] = new_blob_name
        
        # Orchestratorに渡すデータ
        input_data = {
            "mode": "normal",
            "files": file_refs,
            "granularity": granularity,
            "instance_id": instance_id
        }
        
        # input_dataを送信
        await client.raise_event(instance_id, "start_processing", input_data)
        
        response = client.create_check_status_response(req, instance_id)
        response.headers.update(CORS_HEADERS)
        return response
        
    except Exception as e:
        logging.error(f"Starter error: {e}")
        return func.HttpResponse(f"エラー: {str(e)}", status_code=500, headers=CORS_HEADERS)


@app.route(route="upload_diff", methods=["POST", "OPTIONS"])
@app.durable_client_input(client_name="client")
async def upload_diff_starter(req: func.HttpRequest, client) -> func.HttpResponse:
    """差分モード: ファイルをBlobに保存してOrchestrator起動"""
    if req.method == "OPTIONS":
        return func.HttpResponse(status_code=200, headers=CORS_HEADERS)
    
    try:
        new_excel_files = req.files.getlist("newExcelFiles")
        old_structured_md = req.files.get("oldStructuredMd")
        old_test_spec_md = req.files.get("oldTestSpecMd")
        
        if not new_excel_files or not old_structured_md or not old_test_spec_md:
            return func.HttpResponse("必要なファイルがアップロードされていません", status_code=400, headers=CORS_HEADERS)
        
        granularity = req.form.get("granularity", "simple")
        
        # コンテナ作成
        ensure_container_exists("temp-uploads")
        
        # 一時的なinstance_idを生成（Blobパス用）
        import uuid
        temp_id = str(uuid.uuid4())
        
        # ファイルをBlobに保存
        blob_service_client = get_blob_service_client()
        file_refs = []
        
        for idx, file in enumerate(new_excel_files):
            blob_name = f"{temp_id}/input/new_excel_{idx}_{file.filename}"
            blob_client = blob_service_client.get_blob_client(container="temp-uploads", blob=blob_name)
            blob_client.upload_blob(file.stream.read(), overwrite=True)
            file_refs.append({
                "filename": file.filename,
                "blob_name": blob_name,
                "container": "temp-uploads"
            })
        
        # 旧版ファイルも保存
        old_md_blob = f"{temp_id}/input/old_structured.md"
        blob_client = blob_service_client.get_blob_client(container="temp-uploads", blob=old_md_blob)
        blob_client.upload_blob(old_structured_md.stream.read(), overwrite=True)
        
        old_spec_blob = f"{temp_id}/input/old_test_spec.md"
        blob_client = blob_service_client.get_blob_client(container="temp-uploads", blob=old_spec_blob)
        blob_client.upload_blob(old_test_spec_md.stream.read(), overwrite=True)
        
        # Orchestratorを起動（instance_idを先に取得）
        instance_id = await client.start_new("orchestrator")
        
        # instance_idを使ってBlobパスを更新
        for file_ref in file_refs:
            old_blob_name = file_ref["blob_name"]
            new_blob_name = old_blob_name.replace(temp_id, instance_id)
            
            old_blob_client = blob_service_client.get_blob_client(container="temp-uploads", blob=old_blob_name)
            new_blob_client = blob_service_client.get_blob_client(container="temp-uploads", blob=new_blob_name)
            new_blob_client.start_copy_from_url(old_blob_client.url)
            
            file_ref["blob_name"] = new_blob_name
        
        # 旧版ファイルも更新
        new_old_md_blob = old_md_blob.replace(temp_id, instance_id)
        old_blob_client = blob_service_client.get_blob_client(container="temp-uploads", blob=old_md_blob)
        new_blob_client = blob_service_client.get_blob_client(container="temp-uploads", blob=new_old_md_blob)
        new_blob_client.start_copy_from_url(old_blob_client.url)
        
        new_old_spec_blob = old_spec_blob.replace(temp_id, instance_id)
        old_blob_client = blob_service_client.get_blob_client(container="temp-uploads", blob=old_spec_blob)
        new_blob_client = blob_service_client.get_blob_client(container="temp-uploads", blob=new_old_spec_blob)
        new_blob_client.start_copy_from_url(old_blob_client.url)
        
        input_data = {
            "mode": "diff",
            "files": file_refs,
            "old_structured_md_blob": new_old_md_blob,
            "old_test_spec_md_blob": new_old_spec_blob,
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

@app.orchestration_trigger(context_name="context")
def orchestrator(context: df.DurableOrchestrationContext):
    """長時間処理を実行するOrchestrator"""
    try:
        # イベント待機（input_dataを受け取る）
        # リプレイ時は既に受信済みのイベントを再度待たない
        if not context.is_replaying:
            logging.info(f"Orchestrator waiting for event: {context.instance_id}")
        
        input_data = yield context.wait_for_external_event("start_processing")
        
        if not context.is_replaying:
            logging.info(f"Orchestrator received event: {context.instance_id}")
        
        context.set_custom_status({"stage": "structuring", "progress": 10, "message": "設計書を構造化中..."})
        
        # Activity関数を呼び出し（リプレイ時は実行されない）
        result = yield context.call_activity("process_test_generation", input_data)
        
        context.set_custom_status({"stage": "completed", "progress": 100, "message": "完了しました"})
        
        return result
        
    except Exception as e:
        logging.error(f"Orchestrator error: {e}")
        context.set_custom_status({"stage": "failed", "progress": 0, "message": f"エラー: {str(e)}"})
        raise


# ==================== Activity Functions ====================

@app.activity_trigger(input_name="inputData")
def process_test_generation(inputData) -> dict:
    """テスト仕様書生成処理"""
    import io
    from datetime import datetime
    
    # inputDataがJSON文字列の場合はパース
    if isinstance(inputData, str):
        inputData = json.loads(inputData)
    
    mode = inputData["mode"]
    granularity = inputData["granularity"]
    instance_id = inputData["instance_id"]
    
    # 進捗更新用のヘルパー関数
    def update_progress_direct(stage, message, progress):
        try:
            blob_service_client = get_blob_service_client()
            ensure_container_exists("progress")
            blob_client = blob_service_client.get_blob_client("progress", f"{instance_id}.json")
            data = {
                "stage": stage,
                "message": message,
                "progress": progress,
                "timestamp": datetime.utcnow().isoformat()
            }
            blob_client.upload_blob(json.dumps(data, ensure_ascii=False), overwrite=True)
            logging.info(f"進捗更新: {stage} ({progress}%)")
        except Exception as e:
            logging.error(f"進捗更新失敗: {e}")
    
    # core/モジュールが使うProgressManagerにコールバックを設定
    set_progress_callback(update_progress_direct)
    
    blob_service_client = get_blob_service_client()
    
    # ファイルラッパークラス
    class FileWrapper:
        def __init__(self, filename, content):
            self.filename = filename
            self.stream = io.BytesIO(content)
        
        def read(self):
            """core/utils.pyとの互換性のためのreadメソッド"""
            self.stream.seek(0)
            return self.stream.read()
    
    try:
        if mode == "normal":
            # Blobからファイルを取得
            files = []
            for file_ref in inputData["files"]:
                blob_client = blob_service_client.get_blob_client(
                    container=file_ref["container"],
                    blob=file_ref["blob_name"]
                )
                content = blob_client.download_blob().readall()
                files.append(FileWrapper(file_ref["filename"], content))
            
            zip_bytes = normal_mode.generate_normal_test_spec(files, granularity, instance_id)
            filename = "テスト仕様書.zip"
            
        else:
            # 差分モード
            files = []
            for file_ref in inputData["files"]:
                blob_client = blob_service_client.get_blob_client(
                    container=file_ref["container"],
                    blob=file_ref["blob_name"]
                )
                content = blob_client.download_blob().readall()
                files.append(FileWrapper(file_ref["filename"], content))
            
            # 旧版ファイル取得
            blob_client = blob_service_client.get_blob_client(
                container="temp-uploads",
                blob=inputData["old_structured_md_blob"]
            )
            old_structured_content = blob_client.download_blob().readall()
            old_structured_md = FileWrapper("old_structured.md", old_structured_content)
            
            blob_client = blob_service_client.get_blob_client(
                container="temp-uploads",
                blob=inputData["old_test_spec_md_blob"]
            )
            old_spec_content = blob_client.download_blob().readall()
            old_test_spec_md = FileWrapper("old_test_spec.md", old_spec_content)
            
            zip_bytes = diff_mode.generate_diff_test_spec(
                files, old_structured_md, old_test_spec_md, granularity, instance_id
            )
            filename = "テスト仕様書_差分版.zip"
        
        # 結果をBlobに保存
        ensure_container_exists("results")
        blob_name = f"{instance_id}/{filename}"
        blob_client = blob_service_client.get_blob_client(container="results", blob=blob_name)
        blob_client.upload_blob(zip_bytes, overwrite=True)
        
        return {
            "blob_name": blob_name,
            "filename": filename,
            "container": "results"
        }
        
    except Exception as e:
        logging.error(f"Activity error: {e}")
        raise


# ==================== Status & Download ====================

@app.route(route="status/{instanceId}", methods=["GET", "OPTIONS"])
@app.durable_client_input(client_name="client")
async def get_status(req: func.HttpRequest, client) -> func.HttpResponse:
    """進捗状況を取得"""
    if req.method == "OPTIONS":
        return func.HttpResponse(status_code=200, headers=CORS_HEADERS)
    
    instance_id = req.route_params.get("instanceId")
    
    try:
        status = await client.get_status(instance_id)
        
        if not status:
            return func.HttpResponse(
                json.dumps({"error": "ジョブが見つかりません"}, ensure_ascii=False),
                mimetype="application/json",
                status_code=404,
                headers=CORS_HEADERS
            )
        
        # progressコンテナから詳細な進捗を取得
        custom_status = status.custom_status
        try:
            blob_service_client = get_blob_service_client()
            blob_client = blob_service_client.get_blob_client("progress", f"{instance_id}.json")
            progress_data = blob_client.download_blob().readall()
            custom_status = json.loads(progress_data)
        except:
            pass  # progressデータがない場合はOrchestratorのcustomStatusを使用
        
        response_data = {
            "instanceId": instance_id,
            "runtimeStatus": status.runtime_status.name,
            "customStatus": custom_status,
            "createdTime": status.created_time.isoformat() if status.created_time else None,
            "lastUpdatedTime": status.last_updated_time.isoformat() if status.last_updated_time else None
        }
        
        if status.runtime_status == df.OrchestrationRuntimeStatus.Completed:
            response_data["output"] = status.output
        
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


@app.route(route="download/{instanceId}", methods=["GET", "OPTIONS"])
async def download_result(req: func.HttpRequest) -> func.HttpResponse:
    """ZIPファイルをダウンロード"""
    if req.method == "OPTIONS":
        return func.HttpResponse(status_code=200, headers=CORS_HEADERS)
    
    instance_id = req.route_params.get("instanceId")
    
    try:
        blob_service_client = get_blob_service_client()
        container_client = blob_service_client.get_container_client("results")
        
        blobs = list(container_client.list_blobs(name_starts_with=f"{instance_id}/"))
        
        if not blobs:
            return func.HttpResponse("ファイルが見つかりません", status_code=404, headers=CORS_HEADERS)
        
        blob_name = blobs[0].name
        blob_client = blob_service_client.get_blob_client(container="results", blob=blob_name)
        
        zip_bytes = blob_client.download_blob().readall()
        filename = blob_name.split("/")[-1]
        encoded_filename = quote(filename)
        
        headers = {
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
            "Content-Type": "application/zip",
            **CORS_HEADERS
        }
        
        return func.HttpResponse(zip_bytes, status_code=200, headers=headers)
        
    except Exception as e:
        logging.error(f"Download error: {e}")
        return func.HttpResponse(f"エラー: {str(e)}", status_code=500, headers=CORS_HEADERS)
