---
name: apple-hig
description: Apple Human Interface Guidelines 的完整蒸餾，涵蓋設計原則、狀態與回饋、用戶掌控與錯誤恢復、輸入與控制項、視覺基礎、文案寫作、無障礙與平台差異。設計或 review 任何用戶可見行為時使用——狀態呈現、錯誤與等待、確認與撤銷、按鈕與表單、彈層與導航、文案、動畫、深色模式、無障礙。判斷「這樣設計對不對」而手上沒有既定規範時，先來這裡找依據；不要只憑直覺或「看起來還行」下結論。LunaTalk 專屬取捨仍以 DESIGN.md 與 lunatalk-ui-* 為準，本 skill 是它們的上游依據。
---

# Apple Human Interface Guidelines（蒸餾）

蒸餾自 Apple 官方 HIG 全站 158 篇。用途不是照抄 Apple 的樣子，是借它三十年
累積的**判準**——當我們不確定某個設計對不對時，這裡通常已經有答案。

## 怎麼用這份 skill

**先看下面的「四條總則」**，八成的爭議在這裡就能判。需要細節再進對應的
reference；不要一次讀完，那樣只會稀釋注意力。

| 你正在處理 | 讀哪一份 |
|---|---|
| 設計原則、取捨依據、為什麼這樣做 | [references/principles.md](references/principles.md) |
| 載入、等待、錯誤、警告、進度、彈層、通知 | [references/state-and-feedback.md](references/state-and-feedback.md) |
| 撤銷、確認、恢復、啟動、設定、表單輸入、求助 | [references/control-and-recovery.md](references/control-and-recovery.md) |
| 按鈕、文字欄、選單、手勢、鍵盤、清單、導航 | [references/input-and-controls.md](references/input-and-controls.md) |
| 顏色、深色模式、版面、材質、動效、字體、圖示 | [references/visual-foundations.md](references/visual-foundations.md) |
| 任何給用戶看的字 | [references/writing.md](references/writing.md) |
| 無障礙、包容性、右到左語言 | [references/accessibility.md](references/accessibility.md) |
| iOS / iPadOS / macOS 差異，以及對 Web／PWA 的推論 | [references/platform.md](references/platform.md) |

**與既有規範的關係**：本 skill 是上游依據，`DESIGN.md` 與 `lunatalk-ui-*` 是
LunaTalk 的落地取捨。衝突時以我們自己的規範為準——但要在那裡寫下為什麼偏離，
不能默默不一致。

## 四條總則

判斷任何用戶可見行為，先過這四條。答不出來就是設計還沒完成。

### 一、不要把人困住

每一屏都要能回答：**我在哪、能去哪、那裡有什麼、怎麼離開。** 沒有出口的狀態
一律是缺陷。

推論（這條最常被違反）：**系統出問題時，恢復的成本不該由用戶付。** HIG 原話是
「從意外中恢復，不該花掉用戶的時間或工作」，並要求 App 重啟後恢復先前狀態、
不要讓人重走一遍。所以「請重新整理」「請重開 App」不是解法，是把我們的問題
轉嫁出去——何況 PWA 與原生 App 根本沒有「重新整理」。正確做法是**自己恢復**，
真的需要用戶動作時，給一個他按得到的按鈕。

### 二、回饋的份量要匹配資訊的份量

Apple 把回饋分四種：**狀態、完成、警告、錯誤**。狀態應該被動、可忽略；只有
「不可逆且非預期的損失」才值得打斷。

**不要只為了告知而打斷。** 一個有資訊卻無法行動的彈窗，用戶不會感謝——他只會
學會無視所有彈窗。

### 三、看得懂，或做得了

任何狀態至少要滿足一項：用戶**看得懂自己的處境**，或**有事情可以做**。兩項都
不滿足就是缺陷，不是取捨。

延伸：動作按下去**當下**就要有回應，結果要看得見。看不到結果，用戶會以為沒生效
而重複執行——對我們而言那等於重複計費。

### 四、可逆優先於確認

常見且可復原的動作不要跳確認，不常見且不可復原的才要。過度確認會訓練用戶閉眼
按下去，真正危險時反而攔不住。

與其加一道確認，不如把動作做成可撤銷。**寬容比攔阻更尊重人。**

## 八大原則（判斷卡住時的上位框架）

出自 WWDC《Principles of Great Design》。當某個設計「說不上哪裡怪」時，逐條對
一遍通常能指出來：

**目的**、**自主**、**責任**、**熟悉**、**彈性**、**簡潔**、**工藝**、**愉悅**。

展開與各自的判準見 [references/principles.md](references/principles.md)。

## 引用來源

蒸餾自 developer.apple.com/design/human-interface-guidelines 全站（158 篇），
加上 WWDC 設計場次。原文為 Apple 所有；本 skill 只保留判準與理由，不複製原文
全文，也不包含 Apple 的圖像資產。
