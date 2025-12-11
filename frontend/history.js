/**
 * 処理履歴ページ - スクリプト
 * 
 * 機能:
 * - 過去の処理結果一覧表示
 * - ページネーション（1ページあたり10件）
 * - 結果のダウンロード
 * - 結果の削除
 */

// ==================== 環境設定 ====================
// const API_BASE_URL = 'https://poc-func.azurewebsites.net/api'; // 本番環境用
const API_BASE_URL = 'http://localhost:7071/api'; // ローカル開発用
// ==================================================

// ==================== ページネーション設定 ====================
const ITEMS_PER_PAGE = 5;   // 1ページあたりの表示件数
let allResults = [];        // 全ての履歴データ
let currentPage = 1;        // 現在のページ番号

// ==================== 履歴データの読み込み ====================

/**
 * バックエンドから履歴データを取得
 * 
 * /api/list-results を呼び出し、Blob Storageの結果一覧を取得する
 * 取得後、1ページ目を表示
 */
async function loadHistory() {
    try {
        const res = await fetch(`${API_BASE_URL}/list-results`);
        if (!res.ok) throw new Error('履歴の取得に失敗しました');
        
        allResults = await res.json();
        currentPage = 1;
        renderPage();
    } catch (err) {
        document.getElementById('historyBody').innerHTML = 
            `<tr><td colspan="4">エラー: ${err.message}</td></tr>`;
    }
}

// ==================== ページレンダリング ====================

/**
 * 現在のページをレンダリング
 * 
 * 処理フロー:
 * 1. 現在のページに該当するデータを抽出
 * 2. テーブルにHTMLを生成
 * 3. ページネーションボタンを生成
 */
function renderPage() {
    const tbody = document.getElementById('historyBody');
    const pagination = document.getElementById('pagination');
    
    // 履歴が空の場合
    if (allResults.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4">履歴がありません</td></tr>';
        pagination.innerHTML = '';
        return;
    }
    
    // 現在のページに表示するデータを抽出
    const totalPages = Math.ceil(allResults.length / ITEMS_PER_PAGE);
    const start = (currentPage - 1) * ITEMS_PER_PAGE;
    const end = start + ITEMS_PER_PAGE;
    const pageResults = allResults.slice(start, end);
    
    tbody.innerHTML = pageResults.map(item => `
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
    
    renderPagination(totalPages);
}

/**
 * ページネーションボタンを生成
 * 
 * @param {number} totalPages - 総ページ数
 */
function renderPagination(totalPages) {
    const pagination = document.getElementById('pagination');
    
    if (totalPages <= 1) {
        pagination.innerHTML = '';
        return;
    }
    
    let html = `<button onclick="changePage(${currentPage - 1})" ${currentPage === 1 ? 'disabled' : ''}>PREV</button>`;
    
    for (let i = 1; i <= totalPages; i++) {
        if (i === 1 || i === totalPages || Math.abs(i - currentPage) <= 1) {
            html += `<button onclick="changePage(${i})" class="${i === currentPage ? 'active' : ''}">${i}</button>`;
        } else if (i === currentPage - 2 || i === currentPage + 2) {
            html += '<span>...</span>';
        }
    }
    
    html += `<button onclick="changePage(${currentPage + 1})" ${currentPage === totalPages ? 'disabled' : ''}>NEXT</button>`;
    
    pagination.innerHTML = html;
}

/**
 * ページを変更
 * 
 * @param {number} page - 移動先のページ番号
 */
function changePage(page) {
    currentPage = page;
    renderPage();
}

// ==================== ダウンロード処理 ====================

/**
 * 結果をダウンロード
 * 
 * @param {string} instanceId - ジョブID
 * 
 * /api/download/{instanceId} を呼び出し、ZIPファイルをダウンロード
 */
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

// ==================== 削除処理 ====================

/**
 * 結果を削除
 * 
 * @param {string} instanceId - ジョブID
 * 
 * /api/delete/{instanceId} を呼び出し、Blob Storageから結果を削除
 * 削除後、ページを再レンダリング
 */
async function deleteItem(instanceId) {
    if (!confirm('この結果を削除しますか？')) return;
    
    try {
        const res = await fetch(`${API_BASE_URL}/delete/${instanceId}`, {
            method: 'DELETE'
        });
        if (!res.ok) throw new Error('削除に失敗しました');
        
        // 削除したアイテムをリストから除外
        allResults = allResults.filter(item => item.instanceId !== instanceId);
        
        // 現在のページが範囲外になった場合、最後のページに移動
        const totalPages = Math.ceil(allResults.length / ITEMS_PER_PAGE);
        if (currentPage > totalPages && totalPages > 0) {
            currentPage = totalPages;
        }
        
        renderPage();
    } catch (err) {
        alert(`エラー: ${err.message}`);
    }
}

// ==================== ユーティリティ関数 ====================

/**
 * ISO形式の日時を日本語形式に変換
 * 
 * @param {string} isoString - ISO 8601形式の日時文字列
 * @returns {string} 日本語形式の日時（YYYY/MM/DD HH:MM:SS）
 */
function formatDate(isoString) {
    if (!isoString) return '-';
    const date = new Date(isoString);
    
    // UTC時刻をJST（日本標準時）に変換（+9時間）
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

/**
 * バイト数をMB単位に変換
 * 
 * @param {number} bytes - バイト数
 * @returns {string} MB単位のサイズ（小数点2桁）
 */
function formatSize(bytes) {
    if (!bytes) return '-';
    const mb = bytes / (1024 * 1024);
    return `${mb.toFixed(2)} MB`;
}

// ==================== 初期化 ====================

// ページ読み込み時に履歴を読み込む
loadHistory();
