console.log('script.js実行開始');

// ==================== 環境設定 ====================
// const API_BASE_URL = 'https://poc-func.azurewebsites.net/api'; // 本番環境用
const API_BASE_URL = 'http://localhost:7071/api'; // ローカル開発用
// ==================================================

const status = document.querySelector("#status");
const uploadBtn = document.querySelector("#uploadBtn");
const progressBar = document.querySelector("#progressBar");
const progressText = document.querySelector("#progressText");
const progressContainer = document.querySelector("#progressContainer");

console.log('DOM要素取得:', {status, uploadBtn, progressBar, progressText, progressContainer});

let pollingInterval = null;
let currentJobId = null;

// モード切り替え
const modeRadios = document.querySelectorAll('input[name="mode"]');
const normalMode = document.querySelector("#normalMode");
const diffMode = document.querySelector("#diffMode");

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

// ユーザーがアップロードボタンをクリック
uploadBtn.addEventListener("click", async () => {
    console.log('アップロードボタンクリック');
    const mode = document.querySelector('input[name="mode"]:checked').value;
    const granularity = document.querySelector('input[name="granularity"]:checked').value;
    
    const formData = new FormData();
    
    // 通常モード：設計書のみ
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
    // 差分モード：新版設計書 + 旧版MD2つ
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
    status.textContent = mode === "diff" ? "生成中...（差分検知を含むため時間がかかる場合があります）" : "生成中...";
    progressContainer.style.display = "block";
    progressBar.style.width = "0%";
    progressText.textContent = "処理を開始しています...";

    // エンドポイント選択
    const endpoint = mode === "normal" 
        ? `${API_BASE_URL}/upload`
        : `${API_BASE_URL}/upload_diff`;

    try {
        // ジョブを開始（即座にinstanceIdを取得）
        const startRes = await fetch(endpoint, {
            method: "POST",
            body: formData,
        });
        
        if (!startRes.ok) {
            progressContainer.style.display = "none";
            const errorText = await startRes.text();
            status.textContent = `エラー: ${errorText}`;
            uploadBtn.disabled = false;
            return;
        }
        
        const startData = await startRes.json();
        const instanceId = startData.id; // Durable FunctionsのインスタンスID
        currentJobId = instanceId;
        console.log('ジョブ開始:', instanceId);
        
        // ポーリング開始
        startPolling(instanceId);
        
    } catch (err) {
        stopPolling();
        progressContainer.style.display = "none";
        status.textContent = `通信エラー: ${err.message}`;
        uploadBtn.disabled = false;
    }
});

function startPolling(instanceId) {
    stopPolling();
    
    pollingInterval = setInterval(async () => {
        await pollStatus(instanceId);
    }, 10000); // 10秒間隔
    
    // 初回は即座に実行
    pollStatus(instanceId);
}

async function pollStatus(instanceId) {
    try {
        const statusEndpoint = `${API_BASE_URL}/status/${instanceId}`;
        const res = await fetch(statusEndpoint);
        
        if (!res.ok) return;
        
        const data = await res.json();
        
        // 進捗更新
        if (data.customStatus) {
            updateProgress(data.customStatus);
        }
        
        // 完了時
        if (data.runtimeStatus === "Completed") {
            stopPolling();
            await downloadResult(instanceId);
            progressContainer.style.display = "none";
            status.textContent = "✅ 完了しました";
            uploadBtn.disabled = false;
        }
        
        // 失敗時
        if (data.runtimeStatus === "Failed") {
            stopPolling();
            progressContainer.style.display = "none";
            status.textContent = "❌ 処理に失敗しました";
            uploadBtn.disabled = false;
        }
        
    } catch (err) {
        console.error('ポーリングエラー:', err);
    }
}

async function downloadResult(instanceId) {
    try {
        const downloadEndpoint = `${API_BASE_URL}/download/${instanceId}`;
        const res = await fetch(downloadEndpoint);
        
        if (!res.ok) {
            status.textContent = "ダウンロードに失敗しました";
            return;
        }
        
        const blob = await res.blob();
        const contentDisposition = res.headers.get('content-disposition');
        let filename = 'generated_files.zip';
        
        if (contentDisposition) {
            const match = contentDisposition.match(/filename\*=UTF-8''(.+)/);
            if (match) filename = decodeURIComponent(match[1]);
        }
        
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(url);
        
    } catch (err) {
        console.error('ダウンロードエラー:', err);
        status.textContent = "ダウンロードに失敗しました";
    }
}

function stopPolling() {
    if (pollingInterval) {
        clearInterval(pollingInterval);
        pollingInterval = null;
    }
    // currentJobIdはクリアしない（ポーリング中に必要）
}

function updateProgress(data) {
    const { stage, message, progress } = data;
    
    progressBar.style.width = `${progress}%`;
    
    const stageMessages = {
        "structuring": "📄 設計書を構造化中...",
        "diff": "🔍 差分を検知中...",
        "perspectives": "💡 テスト観点を抽出中...",
        "testspec": "📝 テスト仕様書を生成中...",
        "converting": "🔄 成果物を変換中..."
    };
    
    const displayMessage = stageMessages[stage] || message;
    progressText.textContent = `${displayMessage} (${progress}%)`;
}
