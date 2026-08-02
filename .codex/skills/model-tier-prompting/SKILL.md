---
name: model-tier-prompting
description: 為不同能力與執行面的模型設計或精簡 prompt。Use when：撰寫子代理 prompt、把舊 system prompt 或 skill 遷移到 Mysterious 5／Fable 5／Opus 5／GPT-5.6 Soul 等前沿模型、調整 reasoning effort、或診斷過度工程、過度囉嗦、虛報進度、召回率下降等提示問題。
---

# Model-tier prompting

提示詞是模型能力的補集。這個 repo 把 Mysterious 5、Fable 5、Opus 5 與
GPT-5.6 Soul 統一視為 **Fable 5 class**（frontier-agentic）：共用同一套薄
提示政策，不再按供應商或產品名稱分流。預設方向是拆除舊模型腳手架；只有觀察
到具體缺口時才加回針對該缺口的結構。

## 先判定兩件事

1. **能力**：上述四個模型固定使用 Fable 5 class；其他模型才依實測行為歸層。
2. **執行面**：互動 session、headless 子代理、重用 system prompt 的容錯與成本不同。

即使其中一個模型出現局部弱點，也先補那個可觀察缺口，不把它降到舊的
workhorse 厚提示。未知或較弱模型的細節才需要讀 `references/tier-matrix.md`。

## Fable 5 class 預設

這一檔的提示詞只需要四類資訊：任務意圖與完成狀態、真實邊界、可觀察的驗收
證據，以及真正需要暫停的條件。互動或 headless 的差異只改輸入完整度與回傳
契約，不改成逐步操作劇本。

保留：

- 任務意圖與成功狀態；
- 真實的產品／repo／安全邊界；
- 可觀察的驗收與證據；
- 只有在方向會實質改變時才暫停的條件。

刪除：

- 固定思考步驟、逐檔節拍、反覆自我檢查；
- 同一指令的多次重述；
- 無條件 TDD、多代理、grader、held-out sweep；
- 要求展示 chain-of-thought 或進度表演；
- 沒有量測證據的供應商刻板格式（例如看到 GPT 就套 XML）。

努力深度優先用模型的 `effort`／reasoning control 調整，不要把深度寫成冗長
流程。

## 改寫方法

1. 說明目標模型與執行面。
2. 標出每條舊指令要保護的真實需求。
3. 刪掉沒有需求、事故或量測缺口支撐的過程控制。
4. 合併重複規則，改成正面、可判定的結果描述。
5. 保留授權邊界與高風險停止條件。
6. 對高重用或高影響 prompt 才做代表案例 A/B；普通改寫用靜態檢查和少量案例
   即可。

需要具體反模式時讀 `references/anti-patterns.md`；需要完整遷移方法時讀
`references/rewrite-protocol.md`；只有真正要寫 headless 派工 prompt 時才讀
`references/delegation-prompts.md`。

## 輸出

改寫任務交付精簡後 prompt，並簡短列出刪除的舊腳手架、保留的邊界，以及尚未
驗證的行為主張。診斷任務則交付根因、最小調整和驗證缺口；不要為了符合格式
強行重寫整份 prompt 或啟動外部 eval。
