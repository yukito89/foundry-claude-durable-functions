import logging
import io
import zipfile
from pathlib import Path

from core import llm_service
from core import utils

def generate_normal_test_spec(files, granularity: str) -> bytes:
    """
    通常モードでテスト仕様書一式を生成し、ZIPファイルのバイナリデータを返す
    
    Args:
        files: アップロードされたExcelファイルのリスト
        granularity: テスト粒度（"simple" or "detailed"）
    
    Returns:
        bytes: 生成された成果物を含むZIPファイルのバイナリデータ
    """
    logging.info(f"{len(files)}件のファイルから単体テスト生成（通常版）を開始します。")

    # --- 1. Excelファイル群をMarkdownに変換 ---
    logging.info("ExcelファイルをMarkdownに変換中...")
    md_output_first = utils.process_excel_to_markdown(files)
    logging.info("Markdown変換が完了。")
    
    # --- 2. AIによるテスト観点抽出 ---
    logging.info("テスト観点を抽出中...")
    extract_perspectives_prompt = f"--- 設計書 ---\n{md_output_first}"
    md_output_second = llm_service.extract_test_perspectives(extract_perspectives_prompt)
    logging.info("テスト観点抽出が完了。")

    # --- 3. AIによるテスト仕様書生成 ---
    logging.info("テスト仕様書を生成中...")
    test_gen_prompt = f"--- 設計書 ---\n{md_output_first}\n\n--- テスト観点 ---\n{md_output_second}"
    md_output_third = llm_service.create_test_spec(test_gen_prompt, granularity)
    logging.info("テスト仕様書の生成が完了。")

    # --- 4. 成果物の変換 ---
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
    logging.info("ZIPファイルの作成が完了しました。")
    
    return zip_bytes
