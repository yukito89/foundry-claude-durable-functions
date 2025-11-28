# Durable Functions 設計思想と処理フロー詳細

## 目次
1. [なぜ処理が復活するのか](#なぜ処理が復活するのか)
2. [なぜこのままでいいのか](#なぜこのままでいいのか)
3. [バックグラウンド実行のメリット](#バックグラウンド実行のメリット)
4. [詳細な処理フロー](#詳細な処理フロー)
5. [永続化の仕組み](#永続化の仕組み)

---

## なぜ処理が復活するのか

### Durable Functionsの永続化メカニズム

Durable Functionsは、**処理の状態をAzure Storage（`azure-webjobs-hosts`コンテナ）に永続化**します。これにより、以下のような状況でも処理を継続できます：

- サーバーの再起動
- アプリケーションのクラッシュ
- スケールアウト/スケールイン
- デプロイ中の一時停止

### 現在のアーキテクチャにおける復活の流れ

```python
# function_app.py - Starter関数
instance_id = await client.start_new("orchestrator")  # ① Orchestrator起動
# ...ファイル保存処理...
await client.raise_event(instance_id, "start_processing", input_data)  # ② イベント送信
```

```python
# function_app.py - Orchestrator関数
input_data = yield context.wait_for_external_event("start_processing")  # ③ イベント待機
```

**問題が発生するタイミング:**

```
時刻 0秒: Starter関数がOrchestratorを起動（①）
時刻 1秒: Orchestratorが wait_for_external_event で待機状態に入る（③）
         → この時点で状態がAzure Storageに永続化される
時刻 2秒: 【ここでfunc startを再起動】
時刻 3秒: Durable Functionsが永続化された状態を復元
         → 待機中のOrchestratorが復活
時刻 4秒: Starter関数が raise_event でイベントを送信（②）
         → 復活したOrchestratorがイベントを受信して処理開始
```

### 永続化される情報

| 情報 | 保存場所 | 内容 |
|------|---------|------|
| Orchestratorの実行状態 | `azure-webjobs-hosts/instances` | 現在の実行位置、変数の値 |
| 待機中のイベント | `azure-webjobs-hosts/control-*` | wait_for_external_eventの待機情報 |
| Activity関数の実行履歴 | `azure-webjobs-hosts/history` | 完了したActivity関数の結果 |

---

## なぜこのままでいいのか

### 1. 設計上の意図通りの動作

Durable Functionsは、**障害耐性**を提供するために設計されています。待機中のOrchestratorが復活するのは、以下のシナリオで有益です：

#### シナリオA: 正常な処理フロー
```
ユーザー → Starter → Orchestrator起動 → イベント送信 → Activity実行 → 完了
```
- 問題なく処理が完了

#### シナリオB: 再起動が発生した場合
```
ユーザー → Starter → Orchestrator起動 → 【再起動】 → Orchestrator復活 → イベント送信 → Activity実行 → 完了
```
- 再起動後もOrchestratorが復活し、イベントを受信して処理を継続
- **ユーザーは再アップロード不要**

#### シナリオC: イベント送信前に再起動（現在の懸念ケース）
```
ユーザー → Starter → Orchestrator起動 → 【再起動】 → Orchestrator復活（待機中）
                                                    ↓
次のユーザー → Starter → 新しいOrchestrator起動 → イベント送信 → Activity実行 → 完了
                                                    ↓
                                          古いOrchestratorは待機継続（無害）
```
- 古いOrchestratorは待機し続けるが、**新しいリクエストには影響しない**
- 各Orchestratorは独立した`instance_id`を持つため、混線しない

### 2. 自動クリーンアップ機構

Durable Functionsには、古いインスタンスを自動削除する機能があります：

```json
// host.json（設定例）
{
  "extensions": {
    "durableTask": {
      "storageProvider": {
        "maxQueuePollingInterval": "00:00:30"
      },
      "hubName": "TestGenHub",
      "maxConcurrentActivityFunctions": 10,
      "maxConcurrentOrchestratorFunctions": 10
    }
  }
}
```

**自動削除のタイミング:**
- タイムアウト設定（デフォルト: 7日間）
- 手動でのPurge API呼び出し
- Azure Portalからの削除

### 3. リソース消費は最小限

待機中のOrchestratorは、以下の理由でリソースをほとんど消費しません：

| リソース | 消費量 | 理由 |
|---------|--------|------|
| CPU | 0% | 待機中は実行されない |
| メモリ | 0MB | プロセスメモリに常駐しない |
| ストレージ | 数KB | 状態情報のみ（JSON形式） |
| コスト | ほぼ0円 | 実行時間にカウントされない |

---

## バックグラウンド実行のメリット

### 従来のHTTP同期処理の問題点

```python
# 従来の同期処理（非推奨）
@app.route(route="upload")
def upload_sync(req: func.HttpRequest) -> func.HttpResponse:
    files = req.files.getlist("documentFiles")
    
    # ❌ 問題1: HTTP応答230秒制限
    result = generate_test_spec(files)  # 5分かかる処理
    # → 230秒でタイムアウト、処理は中断される
    
    # ❌ 問題2: リトライ不可
    # タイムアウト後、ユーザーは最初からやり直し
    
    # ❌ 問題3: 進捗表示不可
    # 処理中の状態をクライアントに通知できない
    
    return func.HttpResponse(result)
```

### Durable Functionsによる解決

```python
# Durable Functionsによる非同期処理（推奨）
@app.route(route="upload")
async def upload_starter(req: func.HttpRequest, client) -> func.HttpResponse:
    # ✅ 解決1: 即座にレスポンス（3~5秒）
    instance_id = await client.start_new("orchestrator")
    await client.raise_event(instance_id, "start_processing", input_data)
    return client.create_check_status_response(req, instance_id)
    # → HTTP応答は完了、処理はバックグラウンドで継続

@app.activity_trigger(input_name="inputData")
def process_test_generation(inputData) -> dict:
    # ✅ 解決2: 無制限実行
    result = generate_test_spec(files)  # 5分でも10分でもOK
    
    # ✅ 解決3: 進捗表示可能
    update_progress("structuring", "設計書を構造化中...", 10)
    update_progress("testspec", "テスト仕様書を生成中...", 70)
    
    return result
```

### メリット一覧

| メリット | 説明 | 具体例 |
|---------|------|--------|
| **HTTP応答230秒制限の回避** | Starter関数は3~5秒で完了 | 5分の処理でもタイムアウトしない |
| **障害耐性** | サーバー再起動後も処理継続 | デプロイ中でもジョブが失われない |
| **進捗表示** | リアルタイムで状態を通知 | 「構造化中...」「生成中...」を表示 |
| **スケーラビリティ** | 複数ジョブを並列実行 | 10人が同時アップロードしても問題なし |
| **リトライ可能** | 失敗時に自動再試行 | LLM APIの一時的なエラーを自動リトライ |
| **監視・デバッグ** | Azure Portalで実行履歴を確認 | どのステップで失敗したか追跡可能 |

---

## 詳細な処理フロー

### 1. ジョブ開始フロー

```
┌─────────────┐
│ ユーザー     │
└──────┬──────┘
       │ ① POST /api/upload (Excelファイル)
       ▼
┌─────────────────────────────────────────┐
│ Starter関数 (upload_starter)            │
│                                         │
│ 1. Orchestratorを起動                   │
│    instance_id = start_new()            │  ← 永続化開始
│                                         │
│ 2. ファイルをBlobに保存                  │
│    blob_name = f"{instance_id}/..."     │
│                                         │
│ 3. Orchestratorにイベント送信            │
│    raise_event("start_processing")      │
│                                         │
│ 4. instance_idを即座に返却（3~5秒）      │
└─────────────────────────────────────────┘
       │ ② {"id": "abc123-def456", ...}
       ▼
┌─────────────┐
│ フロント     │ ③ 10秒ごとにポーリング開始
│ エンド       │    GET /api/status/abc123-def456
└─────────────┘
```

### 2. Orchestrator実行フロー

```
┌─────────────────────────────────────────┐
│ Orchestrator関数 (orchestrator)         │
│                                         │
│ 1. イベント待機                          │  ← 永続化ポイント①
│    input_data = wait_for_external_event()│
│    ↓                                    │
│    【ここで再起動すると復活する】          │
│    ↓                                    │
│ 2. 進捗ステータス設定                     │
│    set_custom_status({                  │
│      "stage": "structuring",            │
│      "progress": 10                     │
│    })                                   │
│                                         │
│ 3. Activity関数を呼び出し                │  ← 永続化ポイント②
│    result = call_activity(              │
│      "process_test_generation"          │
│    )                                    │
│    ↓                                    │
│    【Activity完了まで待機】               │
│    ↓                                    │
│ 4. 完了ステータス設定                     │
│    set_custom_status({                  │
│      "stage": "completed",              │
│      "progress": 100                    │
│    })                                   │
│                                         │
│ 5. 結果を返却                            │
│    return result                        │
└─────────────────────────────────────────┘
```

### 3. Activity実行フロー（無制限実行）

```
┌─────────────────────────────────────────┐
│ Activity関数 (process_test_generation)  │
│                                         │
│ 1. Blobからファイル取得                  │
│    blob_client.download_blob()          │
│                                         │
│ 2. Excel解析（10秒）                     │
│    update_progress("structuring", 10)   │  → Blob Storage保存
│                                         │
│ 3. LLM呼び出し: 構造化（30秒）            │
│    update_progress("structuring", 20)   │
│                                         │
│ 4. LLM呼び出し: テスト観点（30秒）        │
│    update_progress("perspectives", 40)  │
│                                         │
│ 5. LLM呼び出し: テスト仕様書（60秒）      │
│    update_progress("testspec", 70)      │
│                                         │
│ 6. Excel/CSV変換（5秒）                  │
│    update_progress("converting", 90)    │
│                                         │
│ 7. 結果をBlobに保存                      │
│    blob_client.upload_blob(zip_bytes)   │
│                                         │
│ 8. Blob参照情報を返却                    │
│    return {"blob_name": "...", ...}     │
└─────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────┐
│ Orchestrator関数（再開）                 │
│ result = 上記の戻り値                    │
│ set_custom_status({"stage": "completed"})│
└─────────────────────────────────────────┘
```

### 4. ポーリング・ダウンロードフロー

```
┌─────────────┐
│ フロント     │ 10秒ごとにポーリング
│ エンド       │
└──────┬──────┘
       │ ① GET /api/status/abc123-def456
       ▼
┌─────────────────────────────────────────┐
│ Status関数 (get_status)                 │
│                                         │
│ 1. Orchestratorの状態を取得              │
│    status = client.get_status(id)       │
│                                         │
│ 2. Blobから詳細進捗を取得                │
│    blob_client.download_blob(           │
│      "progress/abc123-def456.json"      │
│    )                                    │
│                                         │
│ 3. レスポンス返却                        │
│    {                                    │
│      "runtimeStatus": "Running",        │
│      "customStatus": {                  │
│        "stage": "testspec",             │
│        "progress": 70                   │
│      }                                  │
│    }                                    │
└─────────────────────────────────────────┘
       │ ② {"runtimeStatus": "Running", ...}
       ▼
┌─────────────┐
│ フロント     │ 進捗バー更新: 70%
│ エンド       │ 「テスト仕様書を生成中...」
└──────┬──────┘
       │ ... 10秒後 ...
       │ ③ GET /api/status/abc123-def456
       ▼
┌─────────────────────────────────────────┐
│ Status関数                              │
│ {"runtimeStatus": "Completed", ...}     │
└─────────────────────────────────────────┘
       │ ④ {"runtimeStatus": "Completed"}
       ▼
┌─────────────┐
│ フロント     │ ⑤ GET /api/download/abc123-def456
│ エンド       │
└──────┬──────┘
       ▼
┌─────────────────────────────────────────┐
│ Download関数 (download_result)          │
│                                         │
│ 1. Blobから結果を取得                    │
│    blob_client.download_blob(           │
│      "results/abc123-def456/テスト.zip"  │
│    )                                    │
│                                         │
│ 2. ZIPファイルを返却                     │
│    return HttpResponse(zip_bytes)       │
└─────────────────────────────────────────┘
       │ ⑥ テスト仕様書.zip
       ▼
┌─────────────┐
│ ユーザー     │ ダウンロード完了
└─────────────┘
```

---

## 永続化の仕組み

### Azure Storageに保存される情報

```
azure-webjobs-hosts/
├── instances/
│   └── abc123-def456/
│       ├── _metadata.json          # Orchestratorのメタデータ
│       └── _state.json              # 実行状態（変数、位置）
├── control-01/
│   └── abc123-def456-start_processing  # 待機中のイベント情報
└── history/
    └── abc123-def456/
        ├── 0-OrchestratorStarted.json
        ├── 1-EventRaised.json
        ├── 2-TaskScheduled.json
        └── 3-TaskCompleted.json
```

### リプレイ機構の動作

Durable Functionsは、**イベントソーシング**パターンを採用しています：

```python
# Orchestrator関数の実行履歴
[
  {"type": "OrchestratorStarted", "timestamp": "2024-01-01T00:00:00Z"},
  {"type": "EventRaised", "name": "start_processing", "input": {...}},
  {"type": "TaskScheduled", "name": "process_test_generation"},
  # ← ここで再起動
]

# 再起動後のリプレイ
# 1. OrchestratorStartedから再実行（is_replaying=True）
# 2. EventRaisedを再実行（履歴から復元）
# 3. TaskScheduledを再実行（履歴から復元）
# 4. 次の処理から通常実行（is_replaying=False）
```

### 再起動時の挙動

| タイミング | 状態 | 再起動後の動作 |
|-----------|------|---------------|
| Starter実行中 | 永続化前 | 処理は失われる（ユーザーは再アップロード） |
| Orchestrator待機中 | 永続化済み | **復活して待機継続** ← 今回のケース |
| Activity実行中 | 永続化済み | Activityを最初から再実行 |
| 処理完了後 | 永続化済み | 結果は保持、再実行不要 |

---

## まとめ

### なぜ処理が復活するのか
- Durable Functionsは、障害耐性のために**すべての状態をAzure Storageに永続化**する
- `wait_for_external_event`で待機中の状態も永続化される
- 再起動時に、永続化された状態が自動的に復元される

### なぜこのままでいいのか
- 各Orchestratorは独立した`instance_id`を持ち、**混線しない**
- 待機中のOrchestratorは**リソースをほとんど消費しない**
- 自動クリーンアップ機構により、古いインスタンスは削除される
- 再起動後もユーザーのジョブが失われない**障害耐性**を提供

### バックグラウンド実行のメリット
- **HTTP応答230秒制限を完全回避**（Starter関数は3~5秒で完了）
- **無制限実行**（Activity関数は5分でも10分でも実行可能）
- **リアルタイム進捗表示**（Blob Storage経由でポーリング）
- **障害耐性**（サーバー再起動後も処理継続）
- **スケーラビリティ**（複数ジョブを並列実行）
- **監視・デバッグ**（Azure Portalで実行履歴を確認）

### 推奨事項
- 現在のアーキテクチャは、Durable Functionsの設計思想に沿った**正しい実装**
- 待機中のOrchestratorが復活するのは、**仕様通りの動作**
- コード修正は不要、そのまま運用して問題なし
