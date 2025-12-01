// ==================== 環境設定 ====================
const API_BASE_URL = 'https://poc-func.azurewebsites.net/api'; // 本番環境用
// const API_BASE_URL = 'http://localhost:7071/api'; // ローカル開発用
// ==================================================

async function loadHistory() {
    try {
        const res = await fetch(`${API_BASE_URL}/list-results`);
        if (!res.ok) throw new Error('履歴の取得に失敗しました');
        
        const results = await res.json();
        const tbody = document.getElementById('historyBody');
        
        if (results.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4">履歴がありません</td></tr>';
            return;
        }
        
        tbody.innerHTML = results.map(item => `
            <tr>
                <td>${formatDate(item.timestamp)}</td>
                <td>${item.filename}</td>
                <td>${formatSize(item.size)}</td>
                <td>
                    <button class="btn-download" onclick="download('${item.instanceId}')">ダウンロード</button>
                    <button class="btn-delete" onclick="deleteItem('${item.instanceId}')">削除</button>
                </td>
            </tr>
        `).join('');
    } catch (err) {
        document.getElementById('historyBody').innerHTML = 
            `<tr><td colspan="4">エラー: ${err.message}</td></tr>`;
    }
}

async function download(instanceId) {
    try {
        const res = await fetch(`${API_BASE_URL}/download/${instanceId}`);
        if (!res.ok) throw new Error('ダウンロードに失敗しました');
        
        const blob = await res.blob();
        const contentDisposition = res.headers.get('content-disposition');
        let filename = 'generated_files.zip';
        
        if (contentDisposition) {
            const match = contentDisposition.match(/filename\*=UTF-8''(.+)/);
            if (match) filename = decodeURIComponent(match[1]);
        }
        
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(url);
    } catch (err) {
        alert(`エラー: ${err.message}`);
    }
}

async function deleteItem(instanceId) {
    if (!confirm('この結果を削除しますか？')) return;
    
    try {
        const res = await fetch(`${API_BASE_URL}/delete/${instanceId}`, {
            method: 'DELETE'
        });
        if (!res.ok) throw new Error('削除に失敗しました');
        
        loadHistory();
    } catch (err) {
        alert(`エラー: ${err.message}`);
    }
}

function formatDate(isoString) {
    if (!isoString) return '-';
    const date = new Date(isoString);
    
    // UTC時刻に+9時間して日本時間に変換
    const jstDate = new Date(date.getTime() + (9 * 60 * 60 * 1000));
    
    return jstDate.toLocaleString('ja-JP', { 
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });
}

function formatSize(bytes) {
    if (!bytes) return '-';
    const mb = bytes / (1024 * 1024);
    return `${mb.toFixed(2)} MB`;
}

loadHistory();
