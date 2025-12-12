import logging
import io
import zipfile
from pathlib import Path

from core import llm_service
from core import utils
from core.progress_manager import ProgressManager
from core.cost_calculator import calculate_cost

def generate_normal_test_spec(files, granularity: str, job_id: str = None) -> bytes:
    """
    通常モードでテスト仕様書一式を生成し、ZIPファイルのバイナリデータを返す
    
    Args:
        files: アップロードされたExcelファイルのリスト
        granularity: テスト粒度（"simple" or "detailed"）
    
    Returns:
        bytes: 生成された成果物を含むZIPファイルのバイナリデータ
    """
    logging.info(f"{len(files)}件のファイルから単体テスト生成（通常版）を開始します。")
    logging.info(f"Job ID: {job_id}")
    
    progress = None
    if job_id:
        try:
            progress = ProgressManager()
            logging.info("ProgressManager初期化成功")
        except Exception as e:
            logging.error(f"ProgressManager初期化失敗: {e}")
            progress = None
    
    # --- 1. Excelファイル群をMarkdownに変換 ---
    logging.info("ExcelファイルをMarkdownに変換中...")
    
    # 進捗コールバック関数を定義
    def progress_callback(stage, message, progress_percent):
        if progress:
            progress.update_progress(job_id, stage, message, progress_percent)
    
    md_output_first, total_usage = utils.process_excel_to_markdown(files, progress_callback, job_id)
    
    # トークン使用量をログ出力
    structuring_cost = calculate_cost(total_usage)
    logging.info(f"Markdown変換が完了 - 入力トークン: {total_usage['input_tokens']:,}, "
                 f"出力トークン: {total_usage['output_tokens']:,}, モデル: {total_usage['model']}")
    logging.info(f"構造化コスト: ${structuring_cost:.4f}")
    
    # --- 2. AIによるテスト観点抽出 ---
    if progress:
        progress.update_progress(job_id, "perspectives", "テスト観点を抽出中...", 40)
    logging.info("テスト観点を抽出中...")
    extract_perspectives_prompt = f"--- 設計書 ---\n{md_output_first}"
    md_output_second, perspectives_usage = llm_service.extract_test_perspectives(extract_perspectives_prompt)
    
    perspectives_cost = calculate_cost(perspectives_usage)
    logging.info(f"テスト観点抽出完了 - 入力: {perspectives_usage['input_tokens']:,}tok, "
                 f"出力: {perspectives_usage['output_tokens']:,}tok, モデル: {perspectives_usage['model']}")
    logging.info(f"観点抽出コスト: ${perspectives_cost:.4f}")

    # --- 3. AIによるテスト仕様書生成 ---
    if progress:
        progress.update_progress(job_id, "testspec", "テスト仕様書を生成中...", 70)
    logging.info("テスト仕様書を生成中...")
    test_gen_prompt = f"--- 設計書 ---\n{md_output_first}\n\n--- テスト観点 ---\n{md_output_second}"
    md_output_third, testspec_usage = llm_service.create_test_spec(test_gen_prompt, granularity)
    
    testspec_cost = calculate_cost(testspec_usage)
    logging.info(f"テスト仕様書生成完了 - 入力: {testspec_usage['input_tokens']:,}tok, "
                 f"出力: {testspec_usage['output_tokens']:,}tok, モデル: {testspec_usage['model']}")
    logging.info(f"仕様書生成コスト: ${testspec_cost:.4f}")

    # --- 4. 成果物の変換 ---
    if progress:
        progress.update_progress(job_id, "converting", "成果物を変換中...", 90)
    # Markdown形式のテスト仕様書をExcelとCSV形式に変換
    logging.info("成果物をExcel/CSV形式に変換中...")
    excel_bytes, csv_bytes = utils.convert_md_to_excel_and_csv(md_output_third)
    logging.info("Excel/CSVへの変換が完了。")

    # --- 5. 全成果物をZIPファイルにまとめる ---
    logging.info("全成果物をZIPファイルにまとめています。")
    # ファイル名のベース名を決定（単一ファイルの場合はそのファイル名、複数の場合は"設計書"）
    base_name = Path(files[0].filename).stem if len(files) == 1 else "設計書"
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        # 各成果物をZIPに追加
        zip_file.writestr(f"{base_name}_構造化設計書.md", md_output_first.encode('utf-8'))
        zip_file.writestr(f"{base_name}_テスト観点.md", md_output_second.encode('utf-8'))
        zip_file.writestr(f"{base_name}_テスト仕様書.md", md_output_third.encode('utf-8'))
        zip_file.writestr(f"{base_name}_テスト仕様書.xlsx", excel_bytes)
        zip_file.writestr(f"{base_name}_テスト仕様書.csv", csv_bytes)
    
    zip_buffer.seek(0)
    zip_bytes = zip_buffer.read()
    
    # 合計コストを計算
    total_cost = structuring_cost + perspectives_cost + testspec_cost
    
    logging.info("ZIPファイルの作成が完了しました。")
    logging.info(f"=== 合計コスト: ${total_cost:.4f} ===")
    
    if progress:
        progress.update_progress(job_id, "completed", "完了しました", 100)
    
    return zip_bytes
