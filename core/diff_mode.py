import logging
import io
import zipfile

from core import llm_service
from core import utils
from core.progress_manager import ProgressManager
from core.cost_calculator import calculate_cost

def generate_diff_test_spec(new_excel_files, old_structured_md_file, old_test_spec_md_file, granularity: str, job_id: str = None) -> bytes:
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
    logging.info(f"Job ID: {job_id}")
    
    progress = None
    if job_id:
        try:
            progress = ProgressManager()
            logging.info("ProgressManager初期化成功")
        except Exception as e:
            logging.error(f"ProgressManager初期化失敗: {e}")
            progress = None
    
    # Step 1: 新版Excelを構造化
    logging.info("新版Excelを構造化中...")
    
    # 進捗コールバック関数を定義
    def progress_callback(stage, message, progress_percent):
        if progress:
            progress.update_progress(job_id, stage, message, progress_percent)
    
    new_structured_md, total_usage = utils.process_excel_to_markdown(new_excel_files, progress_callback, job_id)
    
    # トークン使用量をログ出力
    structuring_cost = calculate_cost(total_usage)
    logging.info(f"Markdown変換が完了 - 入力トークン: {total_usage['input_tokens']:,}, "
                 f"出力トークン: {total_usage['output_tokens']:,}, モデル: {total_usage['model']}")
    logging.info(f"構造化コスト: ${structuring_cost:.4f}")
    
    # Step 2: 旧版情報の取得
    # アップロードされた旧版ファイルを読み込む
    logging.info("旧版ファイルを読み込み中...")
    old_structured_md = old_structured_md_file.read().decode('utf-8')
    old_test_spec_md = old_test_spec_md_file.read().decode('utf-8')
    
    # Step 3: 差分検知
    if progress:
        progress.update_progress(job_id, "diff", "差分を検知中...", 40)
    # LLMを使用して旧版と新版の設計書を比較し、変更点を抽出
    logging.info("差分検知中...")
    diff_prompt = f"【旧版設計書】\n{old_structured_md}\n\n【新版設計書】\n{new_structured_md}"
    diff_summary, diff_usage = llm_service.detect_diff(diff_prompt)
    
    diff_cost = calculate_cost(diff_usage)
    logging.info(f"差分検知完了 - 入力: {diff_usage['input_tokens']:,}tok, "
                 f"出力: {diff_usage['output_tokens']:,}tok, モデル: {diff_usage['model']}")
    logging.info(f"差分検知コスト: ${diff_cost:.4f}")
    
    # Step 4: テスト観点抽出（差分考慮）
    if progress:
        progress.update_progress(job_id, "perspectives", "テスト観点を抽出中...", 60)
    # 変更差分を考慮したテスト観点をLLMで抽出
    logging.info("テスト観点抽出中...")
    perspectives_prompt = f"【新版設計書】\n{new_structured_md}\n\n【変更差分】\n{diff_summary}"
    test_perspectives, perspectives_usage = llm_service.extract_perspectives_with_diff(perspectives_prompt)
    
    perspectives_cost = calculate_cost(perspectives_usage)
    logging.info(f"テスト観点抽出完了 - 入力: {perspectives_usage['input_tokens']:,}tok, "
                 f"出力: {perspectives_usage['output_tokens']:,}tok, モデル: {perspectives_usage['model']}")
    logging.info(f"観点抽出コスト: ${perspectives_cost:.4f}")
    
    # Step 5: テスト仕様書生成（差分・旧版考慮）
    if progress:
        progress.update_progress(job_id, "testspec", "テスト仕様書を生成中...", 80)
    # 新版設計書、変更差分、旧版テスト仕様書を考慮してテスト仕様書を生成
    logging.info("テスト仕様書生成中...")
    spec_prompt = (
        f"【新版設計書】\n{new_structured_md}\n\n"
        f"【テスト観点】\n{test_perspectives}\n\n"
        f"【変更差分】\n{diff_summary}\n\n"
        f"【旧版テスト仕様書】\n{old_test_spec_md}"
    )
    test_spec_md, testspec_usage = llm_service.create_test_spec_with_diff(spec_prompt)
    
    testspec_cost = calculate_cost(testspec_usage)
    logging.info(f"テスト仕様書生成完了 - 入力: {testspec_usage['input_tokens']:,}tok, "
                 f"出力: {testspec_usage['output_tokens']:,}tok, モデル: {testspec_usage['model']}")
    logging.info(f"仕様書生成コスト: ${testspec_cost:.4f}")
    
    # Step 6: 成果物の変換
    if progress:
        progress.update_progress(job_id, "converting", "成果物を変換中...", 90)
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
    
    # 合計コストを計算
    total_cost = structuring_cost + diff_cost + perspectives_cost + testspec_cost
    
    logging.info("差分版ZIPファイルの作成が完了しました。")
    logging.info(f"=== 合計コスト: ${total_cost:.4f} ===")
    
    if progress:
        progress.update_progress(job_id, "completed", "完了しました", 100)
    
    return zip_bytes
