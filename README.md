# 📊 JIRA Report Exporter

:::info
這是一個用 Python + FastAPI 製作的自動化報表服務，部署於 Cloud Run 上。
可根據時間區間或專案代碼從 Jira 擷取工時與任務資料，並將產出的報表自動上傳至 Google Cloud Storage (GCS)。 
:::

## 🧩 API 文件

> 根據==時間區間==生成報表：
### `GET /reports/monthly/auto`

```cpp
https://jira-exporter-1075612823060.asia-east1.run.app/reports/monthly/auto
```

### `POST /reports/monthly`

```cpp
https://jira-exporter-1075612823060.asia-east1.run.app/reports/monthly?start_date=2025-09-01&end_date=2025-09-30
```

> 根據==專案==生成報表：
### `POST /reports/projects`

```cpp
https://jira-exporter-1075612823060.asia-east1.run.app/reports/projects?project_key=TWPS250026
```

## ☁️ 部署方式（Cloud Run）

### 1️⃣ 建立必要資源
1. 建立 Cloud Storage Bucket，例如 `my-jira-reports`
2. 建立 Service Account，例如 `jira-report-sa`。並給予以下角色：

| 角色名稱                | 說明                                     |
| ---------------------- | ----------------------------------------|
| `Secret Manager Admin` | 允許服務讀取 Jira 帳號與 Token 等 Secret 值 |
| `Storage Object Admin` | 允許服務將報表上傳至 GCS Bucket             |

> 🔑 這兩個資源必須先存在，因為 Cloud Run Service 在部署時要指定 Service Account 並使用 Bucket。
 
### 2️⃣ 建立 Cloud Run Service
1. 選擇 建立服務
2. 選擇 連接 GitHub Repo（可設定自動部署 Trigger）。
3. 設定 Buildpacks 部署：
    | 欄位                         | 設定值  |
    | --------------------------- | -------|
    | **Build context directory** | `/`    |
    | **Entrypoint**              | 留空 ✅ |
    | **Function target**         | 留空 ✅ |
4. 指定剛剛建立的 🔑 Service Account。
5. 設定環境變數:
    | 變數名稱                    | 範例值                          |
    | ------------------------  | ------------------------------- |
    | `GCS_BUCKET`              | `my-jira-reports`               |
    | `JIRA_DOMAIN`             | `https://company.atlassian.net` |
    | `GCP_PROJECT_NUM`         | `123456789012`                  |
    | `JIRA_EMAIL_SECRET_NAME`  | `jira-email`                    |
    | `JIRA_TOKEN_SECRET_NAME`  | `jira-token`                    |
