# Protected Testcase Write

1. 解析唯一 project、suite；更新時還要讀取 external/internal ID 與 version。
2. 依來源順序準備非空的 `action => expected` 邏輯配對。
3. 預設 `single_step=true`、`allow_multi_row=false`，preview 必須為一個 TestLink row 且 logical count 相符。
4. 顯示 environment、target、內容、row policy、operation ID 與 `preview_digest`，等待明確確認。
5. 以未變更輸入、digest、`write=true` 寫入，要求 `verification_status=verified` 與 audit ID。

多列僅能在使用者針對該 payload 明確要求時使用，且必須同時設 `single_step=false`、`allow_multi_row=true`；任一參數變更都需重新 preview。

若回傳 `verification_failed`、`indeterminate` 或 mismatch：回報可能已寫入、audit ID 與 mismatch fields；不可覆寫、刪除、重建或宣稱成功，只能依同一 operation identity 與 audit 證據 resume。

回報欄位：environment、project/suite、testcase ID/external ID/version、operation ID、mode、row policy、logical/row count、preview digest、verification、expected/readback digest、mismatch fields、audit ID。
