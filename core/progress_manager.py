import json
import os
import logging
from azure.storage.blob import BlobServiceClient
from datetime import datetime, timedelta

# Azure Storage SDKのログレベルを設定
logging.getLogger('azure.core.pipeline.policies.http_logging_policy').setLevel(logging.WARNING)
logging.getLogger('azure.storage.blob').setLevel(logging.WARNING)

class ProgressManager:
    def __init__(self):
        connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
        if not connection_string:
            raise ValueError("AZURE_STORAGE_CONNECTION_STRINGが設定されていません")
        
        self.blob_service_client = BlobServiceClient.from_connection_string(
            connection_string,
            logging_enable=False  # SDKの詳細ログを無効化
        )
        self.container_name = "progress"
        self._ensure_container()
    
    def _ensure_container(self):
        try:
            self.blob_service_client.create_container(self.container_name)
        except:
            pass
    
    def update_progress(self, job_id: str, stage: str, message: str, progress: int):
        try:
            blob_client = self.blob_service_client.get_blob_client(self.container_name, f"{job_id}.json")
            data = {
                "stage": stage,
                "message": message,
                "progress": progress,
                "timestamp": datetime.utcnow().isoformat()
            }
            blob_client.upload_blob(json.dumps(data, ensure_ascii=False), overwrite=True, logging_enable=False)
            logging.info(f"進捗更新: {stage} ({progress}%)")
        except Exception as e:
            logging.error(f"進捗更新失敗: {e}")
    
    def get_progress(self, job_id: str):
        blob_client = self.blob_service_client.get_blob_client(self.container_name, f"{job_id}.json")
        try:
            data = blob_client.download_blob(logging_enable=False).readall()
            return json.loads(data)
        except:
            return None
    
    def get_result_url(self, job_id: str, expiry_days: int = 1):
        """結果ファイルのダウンロードURLを生成（1日間有効）"""
        from azure.storage.blob import generate_blob_sas, BlobSasPermissions
        try:
            container_client = self.blob_service_client.get_container_client("results")
            blobs = list(container_client.list_blobs(name_starts_with=f"{job_id}/"))
            if not blobs:
                return None
            
            blob_name = blobs[0].name
            sas_token = generate_blob_sas(
                account_name=self.blob_service_client.account_name,
                container_name="results",
                blob_name=blob_name,
                account_key=self.blob_service_client.credential.account_key,
                permission=BlobSasPermissions(read=True),
                expiry=datetime.utcnow() + timedelta(days=expiry_days)
            )
            return f"{self.blob_service_client.url}/results/{blob_name}?{sas_token}"
        except:
            return None
    
    def delete_progress(self, job_id: str):
        blob_client = self.blob_service_client.get_blob_client(self.container_name, f"{job_id}.json")
        try:
            blob_client.delete_blob(logging_enable=False)
        except:
            pass
