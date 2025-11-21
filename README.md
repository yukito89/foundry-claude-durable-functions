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

---

## 目次

- [アーキテクチャ構成](#アーキテクチャ構成)
- [前提条件](#前提条件)
- [環境構築](#環境構築)
- [設定](#設定)
  - [.envファイル](#envファイル)
  - [Azure AI Foundryの設定](#azure-ai-foundryの設定)
- [ローカルでの実行](#ローカルでの実行)
- [Azureへのデプロイ](#azureへのデプロイ)
- [主要ファイル構成](#主要ファイル構成)
- [使用技術一覧](#使用技術一覧)

---

## アーキテクチャ構成

### システム全体構成

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────────┐
│   フロントエンド │    │   バックエンド   │    │   LLMサービス        │
│                 │    │                 │    │                     │
│ Azure Static    │───▶│ Azure Functions │───▶│ Azure AI Foundry    │
│ Web Apps        │    │                 │    │ (Claude Sonnet 4.5) │
│                 │    │                 │    │                     │
└─────────────────┘    └─────────────────┘    └─────────────────────┘
```

### モジュール構成

```
testgen-unit-diff/
├── function_app.py          # HTTPエンドポイント（通常版・差分版）
├── core/                    # ビジネスロジック層
│   ├── normal_mode.py       # 通常モード処理
│   ├── diff_mode.py         # 差分モード処理
│   ├── llm_service.py       # LLM呼び出し抽象化
│   └── utils.py             # 共通ユーティリティ
├── frontend/                # フロントエンド
│   ├── index.html
│   ├── script.js
│   └── style.css
└── 単体テスト仕様書.xlsx     # Excelテンプレート
```

### 処理フロー

#### 通常モード
1. **Excel解析**: アップロードされたExcelファイルをMarkdown形式に構造化
2. **テスト観点抽出**: LLMが設計書からテスト観点を抽出
3. **テスト仕様書生成**: LLMがテスト観点を基にテスト仕様書を生成
4. **成果物変換**: MarkdownをExcel/CSV形式に変換
5. **ZIP作成**: 全成果物をZIPファイルにまとめて返却

#### 差分モード
1. **新版Excel解析**: 新版設計書をMarkdown形式に構造化
2. **差分検知**: 旧版と新版の設計書を比較して変更点を抽出
3. **テスト観点抽出**: 差分を考慮したテスト観点を抽出
4. **テスト仕様書生成**: 旧版テスト仕様書を参考に差分版を生成
5. **成果物変換・ZIP作成**: 通常モードと同様

### LLMサービス

`core/llm_service.py`でAzure AI Foundry経由でClaude Sonnet 4.5を利用：

```python
from anthropic import AnthropicFoundry

client = AnthropicFoundry(
    api_key=foundry_api_key,
    base_url=foundry_endpoint
)
```

---

## 前提条件

- [Python 3.11](https://www.python.org/downloads/release/python-3110/)
- [Visual Studio Code](https://code.visualstudio.com/)
- [Node.js 18.x 以降](https://nodejs.org/) （Azure Functions Core Toolsのインストールに必要）
- [Azure アカウント](https://azure.microsoft.com/ja-jp/) （Azure AI FoundryおよびAzureへのデプロイに必要）

---

## 環境構築

### 1. Azure Functions Core Toolsのインストール

Azure Functions Core Toolsは、ローカル環境でAzure Functionsを開発・実行・デバッグするために必要なコマンドラインツールです。

**方法1: npm経由でインストール（推奨）**

1. ターミナルでNode.jsがインストールされていることを確認します。
   ```
   node --version
   ```
   
2. 管理者権限でターミナルを開き、以下を実行します。
   ```
   npm install -g azure-functions-core-tools@4 --unsafe-perm true
   ```

3. インストール完了後、バージョンを確認します。
   ```
   func --version
   ```

**方法2: MSIインストーラーを使用**

1. [Azure Functions Core Tools リリースページ](https://github.com/Azure/azure-functions-core-tools/releases)にアクセスします。
2. 最新バージョンの `Azure.Functions.Cli.win-x64.<version>.msi` をダウンロードします。
3. ダウンロードしたMSIファイルを実行し、インストールウィザードに従います。
4. インストール完了後、新しいターミナルを開いて確認します。
   ```
   func --version
   ```

### 2. Visual Studio Code 拡張機能のインストール

開発を効率化するために、以下の拡張機能をインストールします。

**1. Azure Tools（拡張機能パック）**

1. VS Codeを開きます。
2. 左側のアクティビティバーから「拡張機能」アイコン（四角が4つ並んだアイコン）をクリックするか、`Ctrl+Shift+X`を押します。
3. 検索ボックスに「**Azure Tools**」と入力します。
4. 「Azure Tools」（発行元: Microsoft）を見つけて「インストール」ボタンをクリックします。
5. この拡張機能パックには以下が含まれます：
   - Azure Account
   - Azure Functions
   - Azure Resources
   - Azure Storage
   - Azure App Service
   - その他Azure関連ツール

**2. Python**

1. 拡張機能の検索ボックスに「**Python**」と入力します。
2. 「Python」（発行元: Microsoft）を見つけて「インストール」ボタンをクリックします。
3. この拡張機能により、以下の機能が有効になります：
   - コード補完（IntelliSense）
   - デバッグ機能
   - リンティング
   - コードフォーマット

**3. Pylance（Pythonの言語サーバー）**

1. 拡張機能の検索ボックスに「**Pylance**」と入力します。
2. 「Pylance」（発行元: Microsoft）を見つけて「インストール」ボタンをクリックします。
3. Python拡張機能と連携して、高速な型チェックとコード補完を提供します。

**4. Live Server**

1. 拡張機能の検索ボックスに「**Live Server**」と入力します。
2. 「Live Server」（発行元: Ritwick Dey）を見つけて「インストール」ボタンをクリックします。
3. HTMLファイルを右クリックして「Open with Live Server」を選択すると、ローカルWebサーバーが起動します。

### 3. プロジェクトのセットアップ

1.  **リポジトリのクローン**
    
    VS Codeのターミナルで以下を実行します。
    ```
    git clone <リポジトリのURL>
    cd <プロジェクトディレクトリ>
    ```

2.  **VS Codeでプロジェクトを開く**
    
    ファイルメニューから「フォルダーを開く」でプロジェクトディレクトリを開きます。

3.  **仮想環境の作成と有効化**
    
    VS Codeのターミナル（`Ctrl+@`で開く）で以下を実行します。
    ```
    py -3.11 -m venv .venv
    .venv\Scripts\activate
    ```
    
    **注意:** Python 3.11がインストールされていない場合は、[Python公式サイト](https://www.python.org/downloads/release/python-3110/)からダウンロードしてインストールしてください。

4.  **必要なライブラリのインストール**
    ```
    pip install -r requirements.txt
    ```

5.  **CORS設定（ローカル開発用）**
    
    フロントエンドからAPIを呼び出すために、`local.settings.json`にCORS設定が必要です。
    
    プロジェクトルートに`local.settings.json`ファイルを作成し、以下の内容を記述します：
    
    ```json
    {
        "IsEncrypted": false,
        "Values": {
            "AzureWebJobsStorage": "",
            "FUNCTIONS_WORKER_RUNTIME": "python",
            "AzureWebJobsSecretStorageType": "files"
        },
        "Host": {
            "CORS": "*",
            "LocalHttpPort": 7071
        }
    }
    ```

---

## 設定

### .envファイル

プロジェクトのルートにある`.env.example`をコピーして`.env`ファイルを作成します。このファイルに各種サービスの接続情報を記述します。

VS Codeのターミナルで以下を実行します。
```
copy .env.example .env
```

**注意:** `.env`ファイルには認証情報などの機密情報が含まれるため、絶対にGitでコミットしないでください。`.gitignore`に`.env`が記載されていることを確認してください。

### Azure AI Foundryの設定

2025年11月18日にMicrosoftとAnthropicが提携し、Azure AI FoundryでClaude Sonnet 4.5が利用可能になりました。以下の手順で設定を行います。

#### 1. Azure AI Foundryプロジェクトの作成

1. [Azure AI Foundry](https://ai.azure.com/)にアクセスし、Azureアカウントでサインインします。
2. 新しいプロジェクトを作成します。
3. プロジェクト設定から「Models + endpoints」を選択します。

#### 2. Claude Sonnet 4.5のデプロイ

1. 「+ Deploy model」をクリックします。
2. モデルカタログから「Claude Sonnet 4.5」を検索して選択します。
3. デプロイ名を設定します（例: `claude-sonnet-4-5`）。
4. デプロイを完了します。

#### 3. 接続情報の取得

1. デプロイしたモデルの詳細ページを開きます。
2. 以下の情報をコピーします：
   - **エンドポイントURL**: `https://your-foundry.services.ai.azure.com/anthropic/`
   - **APIキー**: 「Keys and Endpoint」セクションから取得
   - **デプロイ名**: 設定したデプロイ名

#### 4. .envファイルの設定

```.env
# -------------------- Azure AI Foundry (Claude) 接続情報 --------------------
# APIキー (必須)
AZURE_FOUNDRY_API_KEY=<ここにAPIキーを記述>

# エンドポイント (必須)
# 例: https://your-foundry.services.ai.azure.com/anthropic/
AZURE_FOUNDRY_ENDPOINT=<ここにエンドポイントを記述>


# -------------------- モデル選択 --------------------
# 構造化処理用モデル (例: claude-sonnet-4-5)
MODEL_STRUCTURING=claude-sonnet-4-5

# テスト観点抽出用モデル
MODEL_TEST_PERSPECTIVES=claude-sonnet-4-5

# テスト仕様書生成用モデル
MODEL_TEST_SPEC=claude-sonnet-4-5

# 差分検知用モデル
MODEL_DIFF_DETECTION=claude-sonnet-4-5
```

**モデル選択について:**
- 各処理ごとに異なるモデルを指定できます
- Azure AI Foundryでデプロイしたモデルのデプロイ名を指定します
- 使い分けの例:
  - コスト重視: 軽い処理に`gpt-4o-mini`、重い処理に`claude-sonnet-4-5`
  - 品質重視: 全ての処理に`claude-sonnet-4-5`
  - バランス型: 構造化・差分検知に`gpt-4o-mini`、テスト生成に`claude-sonnet-4-5`
- 同じモデルを全てに使用する場合は、全てに同じデプロイ名を設定してください

---

## ローカルでの実行

1.  **Azure Functionsホストの起動**
    
    VS Codeのターミナルで以下を実行します。
    ```
    func start
    ```

2.  起動後、`http://localhost:7071/api/upload` というエンドポイントが利用可能になります。

3.  **関数キーの確認（ローカル開発時）**
    
    ローカル環境では、起動時にターミナルに表示されるマスターキーまたは関数キーを使用します。
    本番環境では、Azureポータルから関数キーを取得します（次セクション参照）。

4.  フロントエンドからの動作確認
    - VS Codeで「Live Server」拡張機能をインストール
    - `frontend/index.html`を右クリック→「Open with Live Server」で起動
    - ブラウザでアクセスキーを入力し、Excelファイルをアップロードして動作確認

---

## Azureへのデプロイ

### バックエンド（Azure Functions）のデプロイ

1.  **Azure Tools拡張機能のインストール**
    VS Codeの拡張機能から「Azure Tools」をインストールします。

2.  **Azureにサインイン**
    VS Codeのサイドバーから「Azure」アイコンをクリックし、「Sign in to Azure」でサインインします。

3.  **Function Appの作成とデプロイ**
    - サイドバーの「Azure」→「Functions」を右クリック
    - 「Create Function App in Azure...」を選択
    - アプリ名、Pythonバージョン（**3.11を推奨**）、リージョンを指定
    - 作成完了後、作成したFunction Appを右クリック→「Deploy to Function App...」を選択
    - **注意:** `単体テスト仕様書.xlsx`がプロジェクトルートに配置されていることを確認してください。デプロイ時に自動的に含まれます。`.funcignore`に`*.xlsx`が記載されている場合は削除してください。

4.  **環境変数の設定**
    - Azureポータルで作成したFunction Appを開く
    - 「設定」→「環境変数」→「+追加」→「アプリケーション設定の追加/編集」
    - `.env`ファイルの内容を1つずつ追加（`AZURE_FOUNDRY_API_KEY`, `AZURE_FOUNDRY_ENDPOINT`, `MODEL_STRUCTURING`, `MODEL_TEST_PERSPECTIVES`, `MODEL_TEST_SPEC`, `MODEL_DIFF_DETECTION`）

5.  **関数キーの取得（セキュリティ設定）**
    - AzureポータルでFunction Appを開く
    - 「関数」→対象の関数（upload）を選択
    - 「関数キー」タブをクリック
    - 「+新しいキーを追加」でキーを作成（例: 名前「client-key」）
    - 作成されたキーの値をコピーし、フロントエンドで使用します
    - **注意:** 関数キーは外部に漏らさないように管理してください

### フロントエンド（Azure Static Web Apps）のデプロイ

1.  **script.jsのAPIエンドポイント変更**
    
    `frontend/script.js`を編集し、Function AppのURLを設定します。
    ```javascript
    const endpoint = 'https://<your-function-app>.azurewebsites.net/api/upload';
    ```

2.  **GitHubリポジトリにプッシュ**
    
    VS Codeのターミナルで以下を実行し、変更をGitHubにプッシュします。
    ```bash
    git add .
    git commit -m "Update endpoint for deployment"
    git push origin main
    ```
    
    **注意:** GitHubリポジトリがまだない場合は、事前にGitHub上でリポジトリを作成し、ローカルリポジトリと連携してください。

3.  **Azure Static Web Appsの作成とデプロイ**
    - Azureポータル (https://portal.azure.com) にアクセス
    - 「リソースの作成」→「Static Web App」を検索して選択
    - 「作成」をクリック
    - 基本設定:
      - サブスクリプション、リソースグループを選択
      - 名前を入力
      - リージョンを選択（例: East Asia）
    - デプロイの詳細:
      - ソース: 「GitHub」を選択
      - GitHubアカウントでサインイン
      - 組織、リポジトリ、ブランチを選択
    - ビルドの詳細:
      - ビルドプリセット: 「Custom」を選択
      - アプリの場所: `/frontend`
      - APIの場所: 空欄（APIはFunction Appで別途デプロイ済み）
      - 出力場所: 空欄
    - 「確認および作成」→「作成」をクリック
    - 作成完了後、GitHub Actionsが自動でデプロイを実行します

4.  **動作確認**
    - Static Web AppsのURLにアクセス
    - アクセスキー欄に関数キーを入力
    - Excelファイルをアップロードして動作確認

---

## 主要ファイル構成

- **`function_app.py`**: メインの処理が記述されたAzure FunctionsのHTTPトリガー関数。
- **`requirements.txt`**: Pythonの依存パッケージリスト。
- **`.env.example`**: 環境変数のテンプレートファイル。
- **`host.json`**: Azure Functionsホストのグローバル設定ファイル。ログ設定、拡張機能バンドルのバージョンなど、アプリケーション全体の動作を制御します。
- **`local.settings.json`**: ローカル開発環境専用の設定ファイル。ランタイム設定、CORS設定、ローカル環境変数などを管理します（`.gitignore`で除外済み）。
- **`単体テスト仕様書.xlsx`**: テスト仕様書を生成する際の書き込み先テンプレートExcelファイル。プロジェクトルートに配置し、Azure Functionsと一緒にデプロイされます。
- **`frontend/index.html`, `frontend/script.js`, `frontend/style.css`**: 簡単な動作確認用のフロントエンドファイル。

---

## 使用技術一覧

本プロジェクトで利用している主要なライブラリ、開発ツール、およびクラウドサービスは以下の通りです。

### 1. Pythonライブラリ (`requirements.txt`)

-   `azure-functions`: Azure Functionsのトリガーやバインディングなど、Pythonでの関数開発を可能にするためのコアライブラリ。
-   `anthropic`: Anthropic Claude APIクライアントライブラリ。Azure AI Foundry経由でClaude Sonnet 4.5を呼び出すために使用。
-   `python-dotenv`: `.env`ファイルから環境変数を読み込むために使用。ローカル開発で接続情報を管理します。
-   `pandas`: データ操作とExcelファイルの読み込みに使用。
-   `openpyxl`: Excelファイルの書き込みと操作に使用。

### 2. 開発・デプロイツール

-   **Azure Functions Core Tools**:
    ローカル環境でAzure Functionsを開発、実行、デバッグするためのコマンドラインツール (`func`コマンド)。`func start`でのローカルテストに必須です。

-   **Visual Studio Code 拡張機能**:
    -   **Azure Tools**: Azure Functions, App Service, Storageなど、AzureリソースをVS CodeのGUI上から直接操作・管理・デプロイできる統合拡張機能パック。
    -   **Python**: VS CodeでPythonのコード補完、デバッグ、IntelliSenseなどを有効にするための必須拡張機能。
    -   **Pylance**: Python拡張機能と連携し、高速な型チェックとコード補完を提供するPython言語サーバー。
    -   **Live Server**: ローカル開発時にHTMLファイルを簡易Webサーバーで起動するための拡張機能。

### 3. 利用しているクラウドサービス

-   **Azure Functions**:
    サーバーレスでコードを実行するためのコンピューティングサービス。本プロジェクトのバックエンド処理はこの上で動作します。

-   **Azure Static Web Apps**:
    静的コンテンツ（HTML、CSS、JavaScript）をホスティングするためのサービス。本プロジェクトのフロントエンドをデプロイします。

-   **Azure AI Foundry**:
    Microsoft Azure上で提供される、Claude Sonnet 4.5などの大規模言語モデルを利用するためのサービス。2024年11月18日のMicrosoftとAnthropicの提携により利用可能になりました。
