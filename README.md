# 設計書からのテスト仕様書自動生成アプリケーション（現在は単体テスト仕様書のみ対応可能、結合テスト仕様書にも対応予定）

このアプリケーションは、Excel形式の設計書をアップロードすると、LLM（大規模言語モデル）を利用して構造化された設計書、テスト観点、そして単体テスト仕様書を自動生成するAzure Functionsアプリケーションです。

バックエンドのLLMは、**Azure AI Foundry** 経由で **Claude Sonnet 4.5** を使用します。

## 主な機能

- Excel形式の設計書（`.xlsx`）をHTTP POSTで受け付けます。
- アップロードされたExcelを解析し、内容をMarkdown形式で構造化します。
- 構造化された情報をもとに、LLMがテスト観点を抽出します。
- 抽出されたテスト観点から、LLMが単体テスト仕様書（Markdown形式）を生成します。
- 生成されたMarkdownを`単体テスト仕様書.xlsx`テンプレートに書き込みます。
- 最終的な成果物（構造化設計書.md, テスト観点.md, テスト仕様書.md, テスト仕様書.xlsx）をZIPファイルにまとめて返却します。
- **リアルタイム進捗表示**: Azure Blob Storageを利用し、処理の進捗状況をUIに10秒間隔で反映します。

---

## 目次

- [アーキテクチャ構成](#アーキテクチャ構成)
- [前提条件](#前提条件)
- [環境構築](#環境構築)
- [設定](#設定)
- [ローカルでの実行](#ローカルでの実行)
- [Azureへのデプロイ](#azureへのデプロイ)
- [進捗表示機能](#進捗表示機能)
- [トラブルシューティング](#トラブルシューティング)
- [主要ファイル構成](#主要ファイル構成)
- [使用技術一覧](#使用技術一覧)

---

## アーキテクチャ構成

### システム全体構成

```
┌─────────────────┐    ┌──────────────────────────────────┐    ┌─────────────────────┐
│   フロントエンド │    │   バックエンド (Durable Functions)│    │   LLMサービス        │
│                 │    │                                  │    │                     │
│ Azure Static    │───▶│ Starter → Orchestrator          │───▶│ Azure AI Foundry    │
│ Web Apps        │    │              ↓                   │    │ (Claude Sonnet 4.5) │
│                 │    │           Activity               │    │                     │
└─────────────────┘    └──────────────────────────────────┘    └─────────────────────┘
        │                      │
        │ 10秒ポーリング        │
        └──────────────────────┘
                 │
                 ▼
          ┌─────────────┐
          │ Azure Blob  │
          │ Storage     │
          │ (進捗管理・  │
          │  結果保存)   │
          └─────────────┘
```

### Durable Functions構成

**HTTP応答230秒制限を回避するため、非同期アーキテクチャを採用:**

1. **Starter関数（3~5秒で完了）**
   - ファイルをBlobに保存
   - Orchestratorを起動
   - instanceIdを即座に返却 → HTTP応答完了

2. **Orchestrator関数（バックグラウンド実行）**
   - 処理全体を管理
   - 進捗状態を保持
   - Activity関数を呼び出し

3. **Activity関数（無制限実行）**
   - 実際のテスト生成処理を実行
   - 既存のcore/モジュールを呼び出し
   - 処理時間: 5分でも10分でも無制限

4. **クライアント（10秒間隔ポーリング）**
   - `/api/status/{instanceId}` で進捗確認
   - 完了時に `/api/download/{instanceId}` でダウンロード

### モジュール構成

```
testgen-unit-diff/
├── function_app.py          # Durable Functions定義
├── core/                    # ビジネスロジック層
│   ├── normal_mode.py       # 通常モード処理
│   ├── diff_mode.py         # 差分モード処理
│   ├── llm_service.py       # LLM呼び出し抽象化
│   ├── progress_manager.py  # 進捗管理（Azure Blob Storage）
│   └── utils.py             # 共通ユーティリティ
├── frontend/                # フロントエンド
│   ├── index.html
│   ├── script.js
│   └── style.css
└── 単体テスト仕様書.xlsx     # Excelテンプレート
```

### 処理フロー

#### 通常モード
1. **ファイルアップロード**: Starter関数がファイルをBlobに保存し、instanceIdを即座に返却
2. **Excel解析**: Activity関数がExcelファイルをMarkdown形式に構造化
3. **テスト観点抽出**: LLMが設計書からテスト観点を抽出
4. **テスト仕様書生成**: LLMがテスト観点を基にテスト仕様書を生成
5. **成果物変換**: MarkdownをExcel/CSV形式に変換
6. **結果保存**: ZIPファイルをBlobに保存し、ダウンロードURLを提供

#### 差分モード
1. **ファイルアップロード**: Starter関数が新旧ファイルをBlobに保存し、instanceIdを即座に返却
2. **新版Excel解析**: Activity関数が新版設計書をMarkdown形式に構造化
3. **差分検知**: 旧版と新版の設計書を比較して変更点を抽出
4. **テスト観点抽出**: 差分を考慮したテスト観点を抽出
5. **テスト仕様書生成**: 旧版テスト仕様書を参考に差分版を生成
6. **成果物変換・結果保存**: 通常モードと同様

---

## 前提条件

- [Python 3.11](https://www.python.org/downloads/release/python-3110/)
- [Visual Studio Code](https://code.visualstudio.com/)
- [Node.js 18.x 以降](https://nodejs.org/) （Azure Functions Core Toolsのインストールに必要）
- [Azure アカウント](https://azure.microsoft.com/ja-jp/)

---

## 環境構築

### 1. Azure Functions Core Toolsのインストール

**npm経由でインストール（推奨）:**

```bash
node --version  # Node.jsがインストールされていることを確認
npm install -g azure-functions-core-tools@4 --unsafe-perm true
func --version  # インストール確認
```

### 2. Visual Studio Code 拡張機能のインストール

以下の拡張機能をインストール:
- **Azure Tools** (Microsoft)
- **Python** (Microsoft)
- **Pylance** (Microsoft)
- **Live Server** (Ritwick Dey)

### 3. プロジェクトのセットアップ

```bash
git clone <リポジトリのURL>
cd <プロジェクトディレクトリ>
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 4. local.settings.jsonの作成

プロジェクトルートに`local.settings.json`を作成:

```json
{
    "IsEncrypted": false,
    "Values": {
        "AzureWebJobsStorage": "<Azure Storage接続文字列>",
        "AZURE_STORAGE_CONNECTION_STRING": "<Azure Storage接続文字列>",
        "FUNCTIONS_WORKER_RUNTIME": "python",
        "AzureWebJobsSecretStorageType": "files"
    },
    "Host": {
        "CORS": "*",
        "LocalHttpPort": 7071
    }
}
```

**重要**: `AzureWebJobsStorage`と`AZURE_STORAGE_CONNECTION_STRING`には、[設定](#設定)セクションで取得したAzure Storageの接続文字列を設定してください。

---

## 設定

### 1. Azure Storage Accountの作成

#### ストレージアカウントの作成

1. [Azureポータル](https://portal.azure.com)にアクセス
2. 「リソースの作成」→「ストレージアカウント」を検索
3. 基本設定:
   - **ストレージアカウント名**: 一意の名前（例: `testgenprogress`）
   - **地域**: Function Appと同じリージョン推奨
   - **パフォーマンス**: Standard
   - **冗長性**: ローカル冗長ストレージ (LRS)
4. 「確認および作成」→「作成」

#### 接続文字列の取得

1. 作成したストレージアカウントを開く
2. 「アクセスキー」→「キーの表示」
3. **key1**の「接続文字列」をコピー

### 2. .envファイルの設定

`.env.example`をコピーして`.env`を作成:

```bash
copy .env.example .env
```

`.env`ファイルに以下を設定:

```env
# Azure AI Foundry (Claude) 接続情報
AZURE_FOUNDRY_API_KEY=<APIキー>
AZURE_FOUNDRY_ENDPOINT=<エンドポイントURL>

# モデル選択
MODEL_STRUCTURING=claude-sonnet-4-5
MODEL_TEST_PERSPECTIVES=claude-sonnet-4-5
MODEL_TEST_SPEC=claude-sonnet-4-5
MODEL_DIFF_DETECTION=claude-sonnet-4-5

# Azure Storage 接続情報
AZURE_STORAGE_CONNECTION_STRING=<接続文字列>
```

### 3. Azure AI Foundryの設定

1. [Azure AI Foundry](https://ai.azure.com/)にアクセス
2. 新しいプロジェクトを作成
3. 「Models + endpoints」→「+ Deploy model」
4. 「Claude Sonnet 4.5」を検索して選択
5. デプロイ名を設定（例: `claude-sonnet-4-5`）
6. エンドポイントURLとAPIキーを`.env`に設定

---

## ローカルでの実行

```bash
func start
```

起動後、`http://localhost:7071/api/upload` が利用可能になります。

フロントエンドの起動:
1. `frontend/index.html`を右クリック
2. 「Open with Live Server」を選択
3. ブラウザでExcelファイルをアップロード

---

## Azureへのデプロイ

### Durable Functionsの前提条件

**重要**: 以下のプランが必要です（Consumptionプランでは動作しません）:

- **Premium プラン（EP1）**: 約 ¥20,000/月
- **Flex Consumption**: 使用量に応じた従量課金
- **Dedicated (App Service)**: 既存のApp Serviceプランを流用可能

### バックエンド（Azure Functions）のデプロイ

1. **Function Appの作成**
   - VS Codeの「Azure」→「Functions」を右クリック
   - 「Create Function App in Azure...」を選択
   - アプリ名、Pythonバージョン（**3.11**）、リージョンを指定

2. **デプロイ**
   - 作成したFunction Appを右クリック
   - 「Deploy to Function App...」を選択

3. **環境変数の設定**
   - Azureポータルで作成したFunction Appを開く
   - 「設定」→「環境変数」→「+追加」
   - 以下の環境変数を追加:
     - `AZURE_FOUNDRY_API_KEY`
     - `AZURE_FOUNDRY_ENDPOINT`
     - `MODEL_STRUCTURING`
     - `MODEL_TEST_PERSPECTIVES`
     - `MODEL_TEST_SPEC`
     - `MODEL_DIFF_DETECTION`
     - `AZURE_STORAGE_CONNECTION_STRING`
     - **`AzureWebJobsStorage`** ← Durable Functions用（`AZURE_STORAGE_CONNECTION_STRING`と同じ値）

### フロントエンド（Azure Static Web Apps）のデプロイ

1. `frontend/script.js`のエンドポイントURLを本番環境に変更
2. GitHubにプッシュ
3. Azureポータルで「Static Web App」を作成
4. GitHubリポジトリと連携してデプロイ

---

## 進捗表示機能

### 概要
Azure Blob Storageを利用して、バックエンドの処理進捗をリアルタイムでUIに反映します。フロントエンドは10秒間隔でポーリングを行い、進捗状況を取得します。

### 進捗ステージ

| ステージ | 進捗率 | 表示メッセージ |
|---------|--------|---------------|
| structuring | 10% | 📄 設計書を構造化中... |
| diff | 30% | 🔍 差分を検知中... (差分モードのみ) |
| perspectives | 40-50% | 💡 テスト観点を抽出中... |
| testspec | 70% | 📝 テスト仕様書を生成中... |
| converting | 90% | 🔄 成果物を変換中... |
| completed | 100% | ✅ 完了しました |

### Blob Storage コンテナ構成

以下のコンテナが自動作成されます:

| コンテナ名 | 用途 | ライフサイクル |
|-----------|------|---------------|
| `temp-uploads` | 入力ファイル一時保存 | 手動削除推奨 |
| `results` | 生成結果のZIPファイル | 手動削除推奨 |
| `progress` | 進捗情報 | 自動上書き |
| `azure-webjobs-hosts` | Durable Functions制御情報 | 自動管理 |

### ライフサイクル管理（推奨）

古いファイルを自動削除してストレージコストを削減:

**Azure Portal → Storage Account → データ管理 → ライフサイクル管理**

```json
{
  "rules": [{
    "name": "cleanup-temp-files",
    "enabled": true,
    "definition": {
      "filters": {
        "prefixMatch": ["temp-uploads/", "results/"]
      },
      "actions": {
        "baseBlob": {
          "delete": {"daysAfterModificationGreaterThan": 1}
        }
      }
    }
  }]
}
```

---

## トラブルシューティング

### Durable Functions関連

#### エラー: "AzureWebJobsStorage が設定されていません"
**原因**: Durable Functionsに必要なストレージ接続文字列が未設定

**解決策**:
- ローカル: `local.settings.json` に `AzureWebJobsStorage` を追加
- 本番: Azure Portal で環境変数 `AzureWebJobsStorage` を追加

#### エラー: "results コンテナが見つかりません"
**原因**: 結果保存用のBlobコンテナが未作成

**解決策**:
- 自動作成されるため、初回実行後に再試行
- または手動でコンテナ `results` を作成

#### 進捗が更新されない
**原因**: ポーリング間隔が長すぎる、またはCORS設定の問題

**解決策**:
- `script.js` のポーリング間隔を確認（現在10秒）
- Azure Portal → Function App → CORS設定を確認

#### ダウンロードが開始されない
**原因**: Blob URLの有効期限切れ、またはアクセス権限の問題

**解決策**:
- `/download/{instanceId}` エンドポイントを使用（Blob URLを直接使用しない）
- Storage Accountのファイアウォール設定を確認

### 進捗表示関連

#### 進捗が10%で止まる
**原因**: `progress`コンテナへの書き込み失敗

**解決策**: `AZURE_STORAGE_CONNECTION_STRING`を確認

#### ダウンロードが404エラー
**原因**: `results`コンテナにファイルが存在しない

**解決策**: Activity関数のログを確認

---

## パフォーマンス特性

### 処理時間の内訳（例）

| 処理 | 時間 | 備考 |
|------|------|------|
| HTTP応答 | 3~5秒 | ✅ 230秒制限を完全回避 |
| Excel解析 | 10秒 | シート数に依存 |
| LLM呼び出し（構造化） | 30秒 | トークン数に依存 |
| LLM呼び出し（テスト観点） | 30秒 | トークン数に依存 |
| LLM呼び出し（テスト仕様書） | 60秒 | トークン数に依存 |
| Excel/CSV変換 | 5秒 | テストケース数に依存 |
| **合計** | **約135秒** | **HTTP応答とは無関係** |

### スケーラビリティ

- **同時実行数**: Premium/Flexプランで自動スケール
- **最大実行時間**: 無制限（Premium/Flex/Dedicated）
- **ファイルサイズ制限**: Blob Storage経由のため実質無制限

---

## 主要ファイル構成

- **`function_app.py`**: Durable Functions定義（Starter, Orchestrator, Activity）
- **`requirements.txt`**: Pythonの依存パッケージリスト
- **`.env.example`**: 環境変数のテンプレートファイル
- **`host.json`**: Azure Functionsホストのグローバル設定ファイル
- **`local.settings.json`**: ローカル開発環境専用の設定ファイル（`.gitignore`で除外済み）
- **`単体テスト仕様書.xlsx`**: テスト仕様書を生成する際の書き込み先テンプレートExcelファイル
- **`frontend/`**: フロントエンドファイル（index.html, script.js, style.css）

---

## 使用技術一覧

### Pythonライブラリ

- `azure-functions`: Azure Functionsのコアライブラリ
- `azure-durable-functions`: Durable Functions用ライブラリ
- `anthropic`: Anthropic Claude APIクライアント
- `python-dotenv`: 環境変数管理
- `pandas`: データ操作とExcelファイル読み込み
- `openpyxl`: Excelファイル書き込み
- `azure-storage-blob`: Azure Blob Storage操作

### 開発・デプロイツール

- **Azure Functions Core Tools**: ローカル開発・デバッグツール
- **Visual Studio Code 拡張機能**: Azure Tools, Python, Pylance, Live Server

### クラウドサービス

- **Azure Functions**: サーバーレスコンピューティング
- **Azure Static Web Apps**: 静的コンテンツホスティング
- **Azure AI Foundry**: Claude Sonnet 4.5などのLLMサービス
- **Azure Blob Storage**: 進捗管理・結果保存・Durable Functions状態管理

---

## 参考資料

- [Azure Durable Functions 公式ドキュメント](https://learn.microsoft.com/ja-jp/azure/azure-functions/durable/)
- [Python Durable Functions](https://learn.microsoft.com/ja-jp/azure/azure-functions/durable/quickstart-python-vscode)
- [Blob Storage ライフサイクル管理](https://learn.microsoft.com/ja-jp/azure/storage/blobs/lifecycle-management-overview)
- [Function App プラン比較](https://learn.microsoft.com/ja-jp/azure/azure-functions/functions-scale)
