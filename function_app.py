import azure.functions as func
import logging
from urllib.parse import quote

from core import normal_mode, diff_mode

# FunctionAppの初期化（IP制限を使用するため認証レベルをANONYMOUSに変更）
app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

# --- 定数 ---
MAX_FILES = 10  # アップロード可能な最大ファイル数
MAX_FILE_SIZE = 50 * 1024 * 1024  # アップロード可能な最大ファイルサイズ（50MB）

@app.route(route="upload", methods=["POST"])
def upload(req: func.HttpRequest) -> func.HttpResponse:
    """
    通常モードでのテスト仕様書生成リクエストを受け付けるエンドポイント
    """
    try:
        # リクエストからファイルを取得
        files = req.files.getlist("documentFiles")
        if not files:
            return func.HttpResponse("ファイルがアップロードされていません", status_code=400)
        
        # ファイル数の検証
        if len(files) > MAX_FILES:
            return func.HttpResponse(f"ファイル数は{MAX_FILES}件までです", status_code=400)
        
        # 各ファイルの形式とサイズを検証
        for file in files:
            if not file.filename.endswith('.xlsx'):
                return func.HttpResponse("Excelファイル(.xlsx)のみ対応しています", status_code=400)
            
            # ファイルサイズの確認
            file.stream.seek(0, 2)
            if file.stream.tell() > MAX_FILE_SIZE:
                return func.HttpResponse("ファイルサイズが大きすぎます（最大50MB）", status_code=400)
            file.stream.seek(0)
        
        # テスト粒度パラメータの取得と検証
        granularity = req.form.get("granularity", "simple")
        if granularity not in ["simple", "detailed"]:
            granularity = "simple"
            
    except Exception as e:
        logging.error(f"リクエスト検証エラー: {e}")
        return func.HttpResponse("リクエストの形式が正しくありません", status_code=400)

    try:
        # ビジネスロジックの呼び出し（通常モード）
        zip_bytes = normal_mode.generate_normal_test_spec(files, granularity)
        
        # ZIPファイルのレスポンスを生成
        output_filename = f"テスト仕様書.zip"
        encoded_filename = quote(output_filename)  # 日本語ファイル名をURLエンコード
        headers = {
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
            "Content-Type": "application/zip",
            "Access-Control-Expose-Headers": "Content-Disposition"
        }
        return func.HttpResponse(zip_bytes, status_code=200, headers=headers)

    except ValueError as ve:
        logging.error(f"設定またはデータエラー: {ve}")
        return func.HttpResponse(f"処理エラー: {ve}", status_code=400)
    except Exception as e:
        logging.error(f"処理全体で予期せぬエラー: {e}")
        return func.HttpResponse("サーバー内部でエラーが発生しました", status_code=500)


@app.route(route="upload_diff", methods=["POST"])
def upload_diff(req: func.HttpRequest) -> func.HttpResponse:
    """
    差分モードでのテスト仕様書生成リクエストを受け付けるエンドポイント
    """
    try:
        # 新版設計書ファイルの取得
        new_excel_files = req.files.getlist("newExcelFiles")
        if not new_excel_files:
            return func.HttpResponse("新版設計書がアップロードされていません", status_code=400)

        # 旧版構造化設計書の取得
        old_structured_md_file = req.files.get("oldStructuredMd")
        if not old_structured_md_file:
            return func.HttpResponse("旧版構造化設計書がアップロードされていません", status_code=400)
        
        # 旧版テスト仕様書の取得
        old_test_spec_md_file = req.files.get("oldTestSpecMd")
        if not old_test_spec_md_file:
            return func.HttpResponse("旧版テスト仕様書がアップロードされていません", status_code=400)

        # テスト粒度パラメータの取得と検証
        granularity = req.form.get("granularity", "simple")
        if granularity not in ["simple", "detailed"]:
            granularity = "simple"
            
    except Exception as e:
        logging.error(f"リクエスト検証エラー: {e}")
        return func.HttpResponse("リクエストの形式が正しくありません", status_code=400)

    try:
        # ビジネスロジックの呼び出し（差分モード）
        zip_bytes = diff_mode.generate_diff_test_spec(
            new_excel_files, 
            old_structured_md_file, 
            old_test_spec_md_file, 
            granularity
        )
        
        # ZIPファイルのレスポンスを生成
        output_filename = "テスト仕様書_差分版.zip"
        encoded_filename = quote(output_filename)  # 日本語ファイル名をURLエンコード
        headers = {
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
            "Content-Type": "application/zip",
            "Access-Control-Expose-Headers": "Content-Disposition"
        }
        return func.HttpResponse(zip_bytes, status_code=200, headers=headers)

    except ValueError as ve:
        logging.error(f"設定またはデータエラー: {ve}")
        return func.HttpResponse(f"処理エラー: {ve}", status_code=400)
    except Exception as e:
        logging.error(f"差分版処理で予期せぬエラー: {e}")
        return func.HttpResponse("サーバー内部でエラーが発生しました", status_code=500)