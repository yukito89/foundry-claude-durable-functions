# 単体テスト仕様書自動生成アプリケーション

このアプリケーションは、Excel形式の設計書をアップロードすると、LLM（大規模言語モデル）を利用して構造化された設計書、テスト観点、そして単体テスト仕様書を自動生成するAzure Functionsアプリケーションです。

バックエンドのLLMは、**Azure AI Foundry** 経由で **Claude Sonnet 4.5** を使用します。

## 主な機能

- Excel形式の設計書（`.xlsx`）をHTTP POSTで受け付けます。
- アップロードされたExcelを解析し、内容をMarkdown形式で構造化します。
- 構造化された情報をもとに、LLMがテスト観点を抽出します。
- 抽出されたテスト観点から、LLMが単体テスト仕様書（Markdown形式）を生成します。
- 生成されたMarkdownを`単体テスト仕様書.xlsx`テンプレートに書き込みます。
- 最終的な成果物（構造化設計書.md, テスト観点.md, テスト仕様書.md, テスト仕様書.xlsx）をZIPファイルにまとめてBlob Storageに保存します。
- **リアルタイム進捗表示**: Azure Blob Storageを利用し、処理の進捗状況をUIに10秒間隔で反映します。
- **処理履歴管理**: 過去の処理結果を一覧表示し、いつでもダウンロード可能です。ブラウザを閉じても結果を取得できます。

---

## 目次

- [アーキテクチャ構成](#アーキテクチャ構成)
- [前提条件](#前提条件)
- [環境構築・設定](#環境構築・設定)
- [ローカルでの実行](#ローカルでの実行)
- [Azureへのデプロイ](#azureへのデプロイ)
- [Azure Portal上の構成](#azure-portal上の構成)
- [運用・監視](#運用・監視)
- [使用技術一覧](#使用技術一覧)

---

## アーキテクチャ構成

### システム全体構成

```
┌─────────────────┐    ┌──────────────────────────────────┐    ┌─────────────────────┐
│   フロントエンド │    │   バックエンド (Durable Functions)│    │   LLMサービス        │
│                 │    │                                  │    │                     │
│ Azure Static    │───▶│ Starter → Orchestrator           │───▶│ Azure AI Foundry    │
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
   - 完了後、履歴ページから `/api/download/{instanceId}` でダウンロード
   - `/api/list-results` で過去の処理結果一覧を取得

### モジュール構成

```
foundry-claude-durable-functions/
├── function_app.py          # Durable Functions定義
├── core/                    # ビジネスロジック層
│   ├── normal_mode.py       # 通常モード処理
│   ├── diff_mode.py         # 差分モード処理
│   ├── llm_service.py       # LLM呼び出し抽象化
│   ├── progress_manager.py  # 進捗管理（Azure Blob Storage）
│   └── utils.py             # 共通ユーティリティ
├── frontend/                # フロントエンド
│   ├── index.html           # メインページ
│   ├── history.html         # 処理履歴ページ
│   ├── script.js            # メインページロジック
│   ├── history.js           # 履歴ページロジック
│   ├── auth.js              # 認証処理
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
6. **結果保存**: ZIPファイルをBlobに保存

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
- [Git](https://git-scm.com/downloads) （リポジトリのクローン・バージョン管理に必要）
- [GitHub アカウント](https://github.com/signup) （Static Web Appsの自動デプロイに必要）
- [Visual Studio Code](https://code.visualstudio.com/)
- [Node.js 18.x 以降](https://nodejs.org/) （Azure Functions Core Toolsのインストールに必要）
- [Azure アカウント](https://azure.microsoft.com/ja-jp/)

---

## 環境構築・設定

### 1. Azureリソースの作成

#### Azure AI Foundryの設定

1. [Azure AI Foundry](https://ai.azure.com/)にアクセス
2. 新しいプロジェクトを作成
3. 「Models + endpoints」→「+ Deploy model」
4. 以下のモデルをデプロイ:
   - **Claude Haiku 4.5**: 構造化処理用（デプロイ名: `claude-haiku-4-5`）
   - **Claude Sonnet 4.5**: テスト観点抽出・テスト仕様書生成・差分検知用（デプロイ名: `claude-sonnet-4-5`）
5. 各モデルのエンドポイントURLとAPIキーをメモ（後で`.env`に設定）

#### Azure Storage Accountの作成

1. [Azureポータル](https://portal.azure.com)にアクセス
2. 「リソースの作成」→「ストレージアカウント」を検索
3. 基本設定:
   - **ストレージアカウント名**: 一意の名前（例: `poctestgenstorage`）
   - **地域**: East US 2など
   - **パフォーマンス**: Standard
   - **冗長性**: ローカル冗長ストレージ (LRS)
4. 「確認および作成」→「作成」
5. 作成後、「アクセスキー」→「キーの表示」→ **key1**の「接続文字列」をメモ
   - **用途**: `.env`の`AZURE_STORAGE_CONNECTION_STRING`に設定

**注**: Durable Functions状態管理用のStorage Accountは、後述の「バックエンド（Azure Functions）のデプロイ」時にVS Code Azure Toolsが自動作成します。

---

### 2. 開発ツールのインストール

#### Azure Functions Core Tools

```bash
node --version  # Node.jsがインストールされていることを確認
npm install -g azure-functions-core-tools@4 --unsafe-perm true
func --version  # インストール確認
```

#### Azurite（ローカルストレージエミュレータ）

ローカル開発時のDurable Functions状態管理に使用します。

```bash
npm install -g azurite
```

#### Visual Studio Code 拡張機能

以下の拡張機能をインストールしてください:

**Azure Tools (Microsoft)**
- VS Codeの拡張機能タブ（Ctrl+Shift+X）を開く
- 「Azure Tools」で検索
- Microsoft発行の「Azure Tools」をインストール
- 用途: Azure Functionsのデプロイ・管理

**Python (Microsoft)**
- 「Python」で検索
- Microsoft発行の「Python」をインストール
- 用途: Pythonコードの実行・デバッグ

**Pylance (Microsoft)**
- 「Pylance」で検索
- Microsoft発行の「Pylance」をインストール
- 用途: Python言語サーバー（コード補完・型チェック）

**Live Server (Ritwick Dey)**
- 「Live Server」で検索
- Ritwick Dey発行の「Live Server」をインストール
- 用途: フロントエンドのローカルプレビュー（右クリック→「Open with Live Server」）

---

### 3. プロジェクトのセットアップ

```bash
git clone <リポジトリのURL>
cd <プロジェクトディレクトリ>
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

---

### 4. 環境変数の設定

#### .envファイルの作成

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
MODEL_STRUCTURING=claude-haiku-4-5
MODEL_TEST_PERSPECTIVES=claude-sonnet-4-5
MODEL_TEST_SPEC=claude-sonnet-4-5
MODEL_DIFF_DETECTION=claude-sonnet-4-5

# Azure Storage 接続情報（進捗管理・成果物保存用）
# 上記で作成した1つ目のStorage Account（例: poctestgenstorage）の接続文字列
AZURE_STORAGE_CONNECTION_STRING=<接続文字列>
```

#### local.settings.jsonの作成

プロジェクトルートに`local.settings.json`を作成:

```json
{
    "IsEncrypted": false,
    "Values": {
        "AzureWebJobsStorage": "UseDevelopmentStorage=true",
        "FUNCTIONS_WORKER_RUNTIME": "python",
        "AzureWebJobsSecretStorageType": "files"
    },
    "Host": {
        "CORS": "*",
        "LocalHttpPort": 7071
    }
}
```

**ローカル開発時の`AzureWebJobsStorage`設定:**

- **初回（Azurite使用）**: `"UseDevelopmentStorage=true"`
  - Azuriteを起動してから`func start`
  - コスト不要、オフライン開発可能
  
- **Azure Storage使用（オプション）**: 上記で作成したStorage Account（例: poctestgenstorage）の接続文字列
  - ローカル開発でもクラウドストレージを使用（課金発生）
  
- **デプロイ後**: VS Code Azure Toolsが自動作成したStorage Account（例: claudefunc）の接続文字列に変更可能
  - 本番環境と同じストレージを使用する場合

---

## ローカルでの実行

### 1. Azuriteの起動

新しいターミナルウィンドウで以下を実行（起動したまま）:

```bash
azurite --silent --location c:\azurite --debug c:\azurite\debug.log
```

### 2. Functions Appの起動

```bash
func start
```

起動後、`http://localhost:7071/api/upload` が利用可能になります。

### 3. フロントエンドの起動

1. `frontend/index.html`を右クリック
2. 「Open with Live Server」を選択
3. ブラウザでExcelファイルをアップロード
4. 処理完了後、履歴ページでダウンロード

**注意**: ローカル環境では`func start`を起動したままにする必要があります。PCをスリープすると処理が中断されます。

---

## Azureへのデプロイ

### Durable Functionsの前提条件

**重要**: 以下のプランが必要です（Consumptionプランでは動作しません）:

- **Flex Consumption** ← 本アプリで使用：使用量に応じた従量課金でコスト効率が高い
- **Premium プラン（EP1）**: 約 ¥20,000/月
- **Dedicated (App Service)**: 既存のApp Serviceプランを流用可能

### バックエンド（Azure Functions）のデプロイ

1. **Function Appの作成**
   - VS Codeのサイドバーの「Azure」アイコンをクリック（Azure Tools拡張機能が必要）
   - 「Functions」を右クリック→「Create Function App in Azure」を選択
   - アプリ名、Pythonバージョン（**3.11**）、リージョン、認証方法で**Secrets**を指定

1. **デプロイ**
   - 作成したFunction Appを右クリック
   - 「Deploy to Function App...」を選択

2. **環境変数の設定**
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
     - **`AzureWebJobsStorage`** ← デプロイ時に自動設定済み

### フロントエンド（Azure Static Web Apps）のデプロイ

1. **エンドポイントURLの確認と変更**
   - AzureポータルでデプロイしたFunction Appを開く
   - 「概要」ページの「URL」をコピー（例: `https://<your-function-app>.azurewebsites.net`）
   - `frontend/script.js`と`frontend/history.js`の`API_BASE_URL`をコピーしたURLに変更

2. **Static Web Appの作成**
   - VS Codeのサイドバーの「Azure」アイコンをクリック（Azure Tools拡張機能が必要）
   - 「Static Web Apps」を右クリック→「Create Static Web App (Advanced)...」を選択
   - リソースグループ、アプリ名、**Free**プラン、リージョン、GitHubリポジトリを選択
   - ビルド設定（**Custom**）:
     - **App location**: `/frontend`
     - **Api location**: 空欄
     - **Output location**: 空欄

3. **デプロイ**
   - GitHub Actionsが自動でトリガーされ、デプロイが実行されます

---

## Azure Portal上の構成

現状は、以下の3つのリソースグループで構成されています。

### 1. Claude系モデル用リソースグループ（claudefunc）

Claude系モデルを利用したアプリケーションの実行基盤。Functions、Static Web Apps、監視・ログ基盤を含みます。

#### 含まれるリソース

| リソース名 | 種類 | リージョン | 用途 |
|-----------|------|-----------|------|
| claude-func | Function App | Canada Central | Claude系モデルを利用するDurable Functions実行 |
| claude-static | Static Web App | East US 2 | フロントエンドのホスティング |
| claudefunc | Application Insights | Canada Central | アプリケーション監視 |
| claudefunc | Storage Account | Canada Central | Functions状態管理（`azure-webjobs-hosts`コンテナ含む） |
| FLEX-claude-func-2ab4 | App Service Plan (Flex) | Canada Central | Function App実行プラン |
| workspace-claudefunc | Log Analytics Workspace | Canada Central | ログ集約・分析 |

#### CI/CD

- **フロントエンド**: GitHub Actions経由で自動デプロイ（`.github/workflows/`）
- **バックエンド**: VS Code Azure Tools拡張機能から手動デプロイ

---

### 2. GPT系モデル用リソースグループ（pocfunc）

GPT系モデルを利用したアプリケーションの実行基盤。Claude系と同様の構成で独立運用。

#### 含まれるリソース

| リソース名 | 種類 | リージョン | 用途 |
|-----------|------|-----------|------|
| poc-func | Function App | East US | GPT系モデルを利用するDurable Functions実行 |
| poc-static | Static Web App | East US 2 | フロントエンドのホスティング |
| pocfunc | Application Insights | East US | アプリケーション監視 |
| pocfunc | Storage Account | East US | Functions状態管理（`azure-webjobs-hosts`コンテナ含む） |
| FLEX-poc-func-c1d1 | App Service Plan (Flex) | East US | Function App実行プラン |
| workspace-pocfunc | Log Analytics Workspace | East US | ログ集約・分析 |
| Application Insights Smart Detection | Action Group | Global | 異常検知アラート |

---

### 3. 進捗管理・成果物保存・モデルデプロイ用リソースグループ（poc-rg）

プロジェクト成果物の保存とモデルデプロイを担う共通基盤。Claude/GPT両系統で共有。

#### 含まれるリソース

| リソース名 | 種類 | リージョン | 用途 |
|-----------|------|-----------|------|
| poctestgenstorage | Storage Account | East US 2 | 成果物ZIP・進捗情報・アップロードファイル保存 |
| sampleaifoundry-20251211 | Azure AI Foundry | East US 2 | モデルデプロイ・評価・実験管理 |
| firstProject | AI Foundry Project | East US 2 | Claude/GPTモデル管理・プロジェクト実験スペース |

#### ストレージコンテナ構成

**共通基盤ストレージ（poctestgenstorage）:**

| コンテナ名 | 用途 | ライフサイクル |
|-----------|------|---------------|
| `temp-uploads` | 入力ファイル一時保存 | 手動削除推奨 |
| `results` | 生成結果のZIPファイル | 手動削除推奨 |
| `progress` | 進捗情報 | 自動上書き |

**Function App専用ストレージ（claudefunc / pocfunc）:**

| コンテナ名 | 用途 | ライフサイクル |
|-----------|------|---------------|
| `azure-webjobs-hosts` | Durable Functions制御情報 | 自動管理 |

**注**: `azure-webjobs-hosts`コンテナは各Function App専用のStorage Account（claudefunc / pocfunc）内に作成され、Durable Functionsの状態管理に使用されます。

#### ライフサイクル管理（推奨）

古いファイルを自動削除してストレージコストを削減:

**Azure Portal → Storage Account → データ管理 → ライフサイクル管理**

---

### リソースグループ間の関係

```
┌─────────────────────────────────────────────────────────────┐
│  共通基盤RG（進捗管理・成果物保存・モデルデプロイ）             │
│  ┌──────────────────┐  ┌──────────────────────────────┐     │
│  │ poctestgenstorage│  │ Azure AI Foundry (firstProject)│   │
│  │ - temp-uploads   │  │ - Claude Sonnet 4.5          │     │
│  │ - results        │  │ - GPT-5.2                    │     │
│  │ - progress       │  └──────────────────────────────┘     │
│  └──────────────────┘                                       │
└─────────────────────────────────────────────────────────────┘
         ↑                              ↑
         │                              │
    ┌────┴────┐                    ┌────┴────┐
    │         │                    │         │
┌───┴─────────┴───────────┐   ┌────┴─────────┴───────────┐
│ Claude系RG               │  │ GPT系RG                   │
│ - claude-func            │  │ - poc-func                │
│ - claude-static          │  │ - poc-static              │
│ - claudefunc (Storage)   │  │ - pocfunc (Storage)       │
│   └ azure-webjobs-hosts  │  │   └ azure-webjobs-hosts   │
│ - 監視・ログ基盤          │  │ - 監視・ログ基盤           │
└──────────────────────────┘  └───────────────────────────┘
```

---

## 運用・監視

### Function Appのログ確認

#### 過去の実行結果の確認

1. Azure Portal → Function App（例: `claude-func`）を開く
2. 「概要」タブで関数一覧を確認
3. 確認したい関数を選択（例: `process_test_generation`）
4. 上部タブ「呼び出し」に移動
5. 過去の実行履歴が一覧表示される
   - 実行日時、ステータス（成功/失敗）、実行時間を確認可能
   - 各実行をクリックすると詳細ログを確認可能

#### リアルタイムログの確認

1. Azure Portal → Function App → 「概要」タブ
2. 確認したい関数を選択（例: `process_test_generation`）
3. 上部タブ「ログ」に移動
4. 現在実行中の処理のリアルタイムログが表示される

**注**: ログは実行中のみ表示されます。過去のログは「呼び出しなど」から確認してください。

---

### コストの確認

#### スポンサープランでのコスト確認

1. Azure Portal → 「コスト管理と請求」
2. 左サイドバー「Cost Management」を選択
3. 「スポンサー サブスクリプションは Cost Management では現在利用できません」と表示される
4. 「スポンサー ポータルを使用してください」のリンクまたはボタンをクリック
5. スポンサープランポータルに移動してコストを確認

**注**: スポンサープランの場合、Azure Portal標準のコスト管理機能は使用できません。

#### 主なコスト発生リソース

| Service Name | 料金体系 | 概算コスト |
|-------------|---------|-----------|
| Foundry Models | トークン使用量 | モデル・使用量により変動 |
| Functions | 実行時間・メモリ使用量 | 従量課金（使用量に応じて変動） |
| Storage | ストレージ容量・トランザクション | 約￥2-3/GB/月 + トランザクション費用 |
| Log Analytics | データ取り込み量 | 最初の5GB/月は無料、以降約￥300/GB |

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
