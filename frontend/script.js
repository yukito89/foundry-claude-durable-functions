/**
 * テスト仕様書生成アプリケーション - メインスクリプト
 * 
 * 機能:
 * - Excel設計書のアップロード
 * - 通常版/差分版のモード切り替え
 * - バックエンドへのファイル送信
 * - 10秒間隔で進捗ポーリング
 * - 完了後の履歴ページへの誘導
 */

console.log('script.js実行開始');

// ==================== 環境設定 ====================
// const API_BASE_URL = 'https://claude-func.azurewebsites.net/api'; // 本番環境用
const API_BASE_URL = 'http://localhost:7071/api'; // ローカル開発用
// ==================================================

// ==================== DOM要素の取得 ====================
const status = document.querySelector("#status");                     // ステータスメッセージ表示エリア
const uploadBtn = document.querySelector("#uploadBtn");               // アップロードボタン
const progressBar = document.querySelector("#progressBar");           // 進捗バー
const progressText = document.querySelector("#progressText");         // 進捗テキスト
const progressContainer = document.querySelector("#progressContainer"); // 進捗バーコンテナ
const historyLink = document.querySelector("#historyLink");           // 履歴ページリンク

console.log('DOM要素取得:', {status, uploadBtn, progressBar, progressText, progressContainer});

// ==================== グローバル変数 ====================
let pollingInterval = null;  // ポーリング用タイマーID
let currentJobId = null;     // 現在実行中のジョブID

// ==================== モード切り替え（通常版/差分版） ====================
const modeRadios = document.querySelectorAll('input[name="mode"]');
const normalMode = document.querySelector("#normalMode");  // 通常版ファイル入力エリア
const diffMode = document.querySelector("#diffMode");      // 差分版ファイル入力エリア

// モード変更時に表示を切り替え
modeRadios.forEach(radio => {
    radio.addEventListener("change", () => {
        if (radio.value === "normal") {
            normalMode.style.display = "block";
            diffMode.style.display = "none";
        } else {
            normalMode.style.display = "none";
            diffMode.style.display = "block";
        }
    });
});

// ==================== アップロード処理 ====================

/**
 * アップロードボタンクリック時の処理
 * 
 * 処理フロー:
 * 1. モードと粒度を取得
 * 2. ファイルをFormDataに追加
 * 3. バックエンドに送信
 * 4. instanceIdを取得
 * 5. ポーリング開始
 */
uploadBtn.addEventListener("click", async () => {
    console.log('アップロードボタンクリック');
    const mode = document.querySelector('input[name="mode"]:checked').value;
    const granularity = document.querySelector('input[name="granularity"]:checked').value;
    
    const formData = new FormData();
    
    // 通常モード: 設計書のみアップロード
    if (mode === "normal") {
        const files = document.querySelector("#fileInput").files;
        if (files.length === 0) {
            status.textContent = "詳細設計書を選択してください";
            return;
        }
        for (let i = 0; i < files.length; i++) {
            formData.append("documentFiles", files[i]);
        }
    } 
    // 差分モード: 新版設計書 + 旧版構造化設計書 + 旧版テスト仕様書
    else {
        const newExcelFiles = document.querySelector("#newExcelFiles").files;
        const oldStructuredMd = document.querySelector("#oldStructuredMd").files;
        const oldTestSpecMd = document.querySelector("#oldTestSpecMd").files;
        
        if (newExcelFiles.length === 0) {
            status.textContent = "新版の設計書を選択してください";
            return;
        }
        if (oldStructuredMd.length === 0) {
            status.textContent = "旧版の構造化設計書を選択してください";
            return;
        }
        if (oldTestSpecMd.length === 0) {
            status.textContent = "旧版のテスト仕様書を選択してください";
            return;
        }
        
        for (let i = 0; i < newExcelFiles.length; i++) {
            formData.append("newExcelFiles", newExcelFiles[i]);
        }
        formData.append("oldStructuredMd", oldStructuredMd[0]);
        formData.append("oldTestSpecMd", oldTestSpecMd[0]);
    }
    
    formData.append("granularity", granularity);

    uploadBtn.disabled = true;
    historyLink.style.pointerEvents = "none";
    historyLink.style.opacity = "0.5";
    status.textContent = mode === "diff" ? "生成中...（差分検知を含むため時間がかかる場合があります）" : "生成中...";
    progressContainer.style.display = "block";
    progressBar.style.width = "0%";
    progressText.textContent = "処理を開始しています...";

    // モードに応じてエンドポイントを切り替え
    const endpoint = mode === "normal" 
        ? `${API_BASE_URL}/upload`
        : `${API_BASE_URL}/upload_diff`;

    try {
        // バックエンドにファイルを送信（Durable Functionsのジョブを開始）
        // 即座にinstanceIdが返却され、実際の処理はバックグラウンドで実行される
        const startRes = await fetch(endpoint, {
            method: "POST",
            body: formData,
        });
        
        if (!startRes.ok) {
            progressContainer.style.display = "none";
            const errorText = await startRes.text();
            status.textContent = `エラー: ${errorText}`;
            uploadBtn.disabled = false;
            historyLink.style.pointerEvents = "auto";
            historyLink.style.opacity = "1";
            return;
        }
        
        const startData = await startRes.json();
        const instanceId = startData.id; // Durable FunctionsのインスタンスID（ジョブID）
        currentJobId = instanceId;
        console.log('ジョブ開始:', instanceId);
        
        // 10秒間隔で進捗ポーリングを開始
        startPolling(instanceId);
        
    } catch (err) {
        stopPolling();
        progressContainer.style.display = "none";
        status.textContent = `通信エラー: ${err.message}`;
        uploadBtn.disabled = false;
        historyLink.style.pointerEvents = "auto";
        historyLink.style.opacity = "1";
    }
});

// ==================== 進捗ポーリング ====================

/**
 * 進捗ポーリングを開始
 * 
 * @param {string} instanceId - ジョブID
 * 
 * 10秒間隔で/api/status/{instanceId}を呼び出し、
 * 進捗状況を取得してUIを更新する
 */
function startPolling(instanceId) {
    stopPolling(); // 既存のポーリングを停止
    
    // 10秒間隔でポーリング
    pollingInterval = setInterval(async () => {
        await pollStatus(instanceId);
    }, 10000);
    
    // 初回は即座に実行
    pollStatus(instanceId);
}

/**
 * 進捗状況を取得してUIを更新
 * 
 * @param {string} instanceId - ジョブID
 * 
 * レスポンスに含まれる情報:
 * - runtimeStatus: Running, Completed, Failed
 * - customStatus: {stage, message, progress}
 * - output: 完了時の結果情報
 */
async function pollStatus(instanceId) {
    try {
        const statusEndpoint = `${API_BASE_URL}/status/${instanceId}`;
        const res = await fetch(statusEndpoint);
        
        if (!res.ok) {
            stopPolling();
            progressContainer.style.display = "none";
            status.textContent = `❌ サーバーエラー (${res.status})`;
            uploadBtn.disabled = false;
            historyLink.style.pointerEvents = "auto";
            historyLink.style.opacity = "1";
            return;
        }
        
        const data = await res.json();
        
        // 進捗情報があればUIを更新
        if (data.customStatus) {
            updateProgress(data.customStatus);
        }
        
        // 処理完了時: ポーリング停止、履歴ページへのリンクを表示
        if (data.runtimeStatus === "Completed") {
            stopPolling();
            progressContainer.style.display = "none";
            status.innerHTML = '✅ 完了しました　<a href="history.html" style="color: #4CAF50;">📋 履歴ページでダウンロード</a>';
            uploadBtn.disabled = false;
            historyLink.style.pointerEvents = "auto";
            historyLink.style.opacity = "1";
        }
        
        // 処理失敗時: ポーリング停止、エラーメッセージ表示
        if (data.runtimeStatus === "Failed") {
            stopPolling();
            progressContainer.style.display = "none";
            status.textContent = "❌ 処理に失敗しました";
            uploadBtn.disabled = false;
            historyLink.style.pointerEvents = "auto";
            historyLink.style.opacity = "1";
        }
        
    } catch (err) {
        console.error('ポーリングエラー:', err);
        stopPolling();
        progressContainer.style.display = "none";
        status.textContent = `❌ サーバーエラー: ${err.message}`;
        uploadBtn.disabled = false;
        historyLink.style.pointerEvents = "auto";
        historyLink.style.opacity = "1";
    }
}

/**
 * ポーリングを停止
 * 
 * 完了時やエラー時に呼び出される
 */
function stopPolling() {
    if (pollingInterval) {
        clearInterval(pollingInterval);
        pollingInterval = null;
    }
    // currentJobIdはクリアしない（履歴ページで使用する可能性がある）
}

// ==================== 進捗表示更新 ====================

/**
 * 進捗バーとメッセージを更新
 * 
 * @param {Object} data - 進捗情報
 * @param {string} data.stage - 処理ステージ（structuring, perspectives, testspec等）
 * @param {string} data.message - 表示メッセージ
 * @param {number} data.progress - 進捗率（0-100）
 */
function updateProgress(data) {
    const { stage, message, progress } = data;
    
    // 進捗バーの幅を更新
    progressBar.style.width = `${progress}%`;
    
    // ステージごとの表示メッセージ
    const stageMessages = {
        "structuring": "📄 設計書を構造化中...",
        "diff": "🔍 差分を検知中...",
        "perspectives": "💡 テスト観点を抽出中...",
        "testspec": "📝 テスト仕様書を生成中...",
        "converting": "🔄 成果物を変換中..."
    };
    
    // メッセージと進捗率を表示
    const displayMessage = stageMessages[stage] || message;
    progressText.textContent = `${displayMessage} (${progress}%)`;
}
