# UIが最新のクイズを取得する仕組み

## バックエンド側（GetCurrentQuizFunction）

**役割**: DynamoDBから最新のクイズを取得

**処理フロー**:

1. **GSI_Recent（Global Secondary Index）からクエリ**:
   - GSI1PK = "RECENT" で全クイズを対象にGSI1SK（CreatedAt）で自動的にソート
   - ScanIndexForward = False で降順（新しい順）に
   - Limit = 1 で先頭1件（最新）のみ取得

2. **クイズ情報の整形**:
   - questionId（QuestionHash）
   - title、body、category、level、createdAt
   - rubric（採点基準）は含めない（公開情報のみ）

3. **レスポンス返却**:
   - クイズが存在する場合: `{"question": {...}}`
   - クイズが存在しない場合: `{"status": "empty", "message": "まだクイズが出題されていません"}`

**DynamoDB設計のポイント**:

```python
# クイズ保存時にGSI用の属性を設定
item = {
    "QuestionHash": qhash,           # Primary Key
    "GSI1PK": "RECENT",              # GSI Partition Key（固定値）
    "GSI1SK": created_at,            # GSI Sort Key（ISO 8601形式の日時）
    "Question": {"Title": ..., "Body": ...},
    "Rubric": {...},
    "CreatedAt": created_at,
    ...
}
```

この設計により、**O(1)で最新のクイズを取得**（Scanではなく効率的なQuery）。

## フロントエンド側（Host UI / Team UI）

**ポーリング方式**:

- 15秒ごとに `GET /quiz/current` を呼び出し
- 新しいクイズが生成されたら自動的に画面更新
- Host UI、Team UI両方で同じエンドポイントを使用

**実装例（mainTeam.js）**:

```javascript
const POLL_MS = 15000; // 15秒

async function _poll() {
    const fn = _getCurrentQuizFn();
    if (!fn) return;
    
    // 復習モード中はポーリングしない
    if ((state.quizMode || "live") === "review") return;
    
    try {
        await fn({silent: true}); // 静かに同期
    } catch (_) {
        // ポーリングでは失敗しても無視
    }
}

// 初回同期 + 定期ポーリング開始
if (currentFn) {
    await currentFn({silent: true, force: true});
    setInterval(_poll, POLL_MS);
}
```

**重複チェック**:

```javascript
// quizApi.host.js - クイズ生成後のポーリング
const previousQuizId = state.questionId || null;

for (let i = 0; i < maxAttempts; i++) {
    await new Promise(resolve => setTimeout(resolve, 5000));
    
    const currentData = await fetchCurrentQuiz();
    
    if (currentData?.question) {
        const q = currentData.question;
        // 新しいクイズが生成されたかチェック（IDが変わっている）
        if (q.questionId && q.questionId !== previousQuizId) {
            console.log(`[quiz] new quiz detected: ${q.questionId}`);
            setQuestionView(q);
            return q;
        }
    }
}
```

**同期の流れ**:

1. Host UIで「次のクイズ」ボタンを押下
2. StartQuizGenerationFunctionが即座に202を返却
3. バックグラウンドでGetNextQuizFunctionがクイズ生成
4. 生成完了後、DynamoDBに保存（GSI1PK="RECENT"、GSI1SK=現在時刻）
5. Host UI、Team UIが15秒ごとにポーリング
6. GetCurrentQuizFunctionがGSI_Recentから最新1件を取得
7. questionIdが変わっていれば画面更新

**利点**:
- リアルタイム性: 最大15秒の遅延で全クライアントが同期
- シンプル: WebSocketやServer-Sent Eventsが不要
- スケーラブル: クライアント数が増えてもバックエンドの負荷は一定
- 復習モード対応: ポーリングを停止して過去問を表示可能
