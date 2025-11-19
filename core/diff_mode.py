import logging
import io
import zipfile

from core import llm_service
from core import utils

def generate_diff_test_spec(new_excel_files, old_structured_md_file, old_test_spec_md_file, granularity: str) -> bytes:
    """
    差分モードでテスト仕様書一式を生成し、ZIPファイルのバイナリデータを返す
    
    Args:
        new_excel_files: 新版設計書のExcelファイルリスト
        old_structured_md_file: 旧版構造化設計書のMarkdownファイル
        old_test_spec_md_file: 旧版テスト仕様書のMarkdownファイル
        granularity: テスト粒度（"simple" or "detailed"）
    
    Returns:
        bytes: 生成された成果物を含むZIPファイルのバイナリデータ
    """
    logging.info("差分版テスト生成を開始します。")

    # Step 1: 新版Excelを構造化
    # アップロードされた新版ExcelファイルをMarkdown形式に変換
    logging.info("新版Excelを構造化中...")
    new_structured_md = utils.process_excel_to_markdown(new_excel_files)
    
    # Step 2: 旧版情報の取得
    # アップロードされた旧版ファイルを読み込む
    logging.info("旧版ファイルを読み込み中...")
    old_structured_md = old_structured_md_file.read().decode('utf-8')
    old_test_spec_md = old_test_spec_md_file.read().decode('utf-8')
    
    # Step 3: 差分検知
    # LLMを使用して旧版と新版の設計書を比較し、変更点を抽出
    logging.info("差分検知中...")
    diff_prompt = f"【旧版設計書】\n{old_structured_md}\n\n【新版設計書】\n{new_structured_md}"
    diff_summary = llm_service.detect_diff(diff_prompt)
    logging.info("差分検知完了。")
    
    # Step 4: テスト観点抽出（差分考慮）
    # 変更差分を考慮したテスト観点をLLMで抽出
    logging.info("テスト観点抽出中...")
    perspectives_prompt = f"【新版設計書】\n{new_structured_md}\n\n【変更差分】\n{diff_summary}"
    test_perspectives = llm_service.extract_perspectives_with_diff(perspectives_prompt)
    
    # Step 5: テスト仕様書生成（差分・旧版考慮）
    # 新版設計書、変更差分、旧版テスト仕様書を考慮してテスト仕様書を生成
    logging.info("テスト仕様書生成中...")
    spec_prompt = (
        f"【新版設計書】\n{new_structured_md}\n\n"
        f"【テスト観点】\n{test_perspectives}\n\n"
        f"【変更差分】\n{diff_summary}\n\n"
        f"【旧版テスト仕様書】\n{old_test_spec_md}"
    )
    test_spec_md = llm_service.create_test_spec_with_diff(spec_prompt)
    logging.info("テスト仕様書生成完了。")
    
    # Step 6: 成果物の変換
    # Markdown形式のテスト仕様書をExcelとCSV形式に変換（差分モードフラグを有効化）
    logging.info("Excel/CSV変換中...")
    excel_bytes, csv_bytes = utils.convert_md_to_excel_and_csv(test_spec_md, is_diff_mode=True)
    
    # Step 7: ZIP作成
    # 全成果物をZIPファイルにまとめる
    logging.info("ZIP作成中...")
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        zip_file.writestr("新版_構造化設計書.md", new_structured_md.encode('utf-8'))
        zip_file.writestr("差分サマリー.md", diff_summary.encode('utf-8'))
        zip_file.writestr("テスト観点.md", test_perspectives.encode('utf-8'))
        zip_file.writestr("テスト仕様書.md", test_spec_md.encode('utf-8'))
        zip_file.writestr("テスト仕様書.xlsx", excel_bytes)
        zip_file.writestr("テスト仕様書.csv", csv_bytes)
    
    zip_buffer.seek(0)
    zip_bytes = zip_buffer.read()
    
    logging.info("差分版ZIPファイルの作成が完了しました。")
    
    return zip_bytes
