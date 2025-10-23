import os
from datetime import datetime
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, validator
import pandas as pd
from jira_api import JiraAPI, GROUPS, project_data_to_df, filter_df_by_date, user_data_to_df
from google.cloud import storage
from google.cloud import secretmanager

# 建立 FastAPI App
app = FastAPI()

# -----------------------------------
# 從 Google Secret Manager 取得 secret 值
# secret_name 格式: projects/{project_id}/secrets/{secret_id}
# -----------------------------------
def access_secret(secret_name: str, version: str = "latest") -> str:
    client = secretmanager.SecretManagerServiceClient()
    name = f"{secret_name}/versions/{version}"
    try:
        response = client.access_secret_version(name=name)
        return response.payload.data.decode("UTF-8")
    except Exception as e:
        print(f"Failed to access secret {secret_name}: {e}")
        raise


# 環境變數初始化
@app.on_event("startup")
def load_jira_credentials():
    global domain, GCS_BUCKET, jira_email, jira_token,  Jira

    # 環境變數
    domain = os.environ.get("JIRA_DOMAIN")
    print("domain",domain)

    GCS_BUCKET = os.environ.get("GCS_BUCKET")
    print("GCS_BUCKET",GCS_BUCKET)

    project_id = os.environ.get("GCP_PROJECT_NUM")
    print("project_id",project_id)

    email_secret = os.environ.get("JIRA_EMAIL_SECRET_NAME")
    print("email_secret",email_secret)

    token_secret = os.environ.get("JIRA_TOKEN_SECRET_NAME")
    print("token_secret,"token_secret)


    # secret manager
    jira_email = access_secret(f"projects/{project_id}/secrets/{email_secret}")
    print("jira_email",jira_email)

    jira_token = access_secret(f"projects/{project_id}/secrets/{token_secret}")
    print("Jjira_token",jira_token)


    # Jira API 初始化
    Jira = JiraAPI(domain, jira_email, jira_token)
    print("Jira 初始化完成")

# ===== POST Body schema =====
class DateRange(BaseModel):
    start_date: str
    end_date: str

    @validator("start_date", "end_date")
    def check_date_format(cls, v):
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            raise ValueError("Date must be YYYY-MM-DD")
        return v

# -----------------------------------
# 共用報表生成函數
# -----------------------------------
def generate_report(start_date: str, end_date: str):
    print(f"Step 1: 取得 issues")
    issues = Jira.get_active_issues(start_date, end_date)
    print(f"[INFO] 總共取得 {len(issues)} 筆 active issues")

    print(f"Step 2: issues 轉成 projects 結構")
    projects = Jira.trace_project_info_by_issues(issues)
    print(f"[INFO] 對應到 {len(projects)} 個 project")

    
    print(f"Step 3: 逐一補上每個 issue 的 worklogs 與 user info")
    user_data = {}
    for project in projects:
        for issue in project["issues"]:
            issue["worklogs"] = Jira.get_worklog_from_issue_id(issue["key"])
            for wl in issue["worklogs"]:
                user_id = wl.get("owner_id")
                if user_id and user_id not in user_data:
                    user_data[user_id] = Jira.get_user_group_info_from_user_id(user_id)

    print(f"Step 4: 轉換為 DataFrame")
    df = project_data_to_df(projects)
    user_df = user_data_to_df(user_data)
    df = pd.merge(df, user_df, on="worklog_owner_id", how="left")
    print(f"[INFO] 最終資料筆數（含 worklogs）：{len(df)}")

    print(f"Step 5: 時間篩選")
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    filtered_df = filter_df_by_date(df, start, end)
    print(f"[INFO] 過濾後筆數：{len(filtered_df)}")

    print(f"Step 6: 存入GCS")
    filename = f"jiraReport_{start_date}_{end_date}.csv"
    client = storage.Client()
    bucket = client.bucket(GCS_BUCKET)
    blob = bucket.blob(filename)
    blob.upload_from_string(filtered_df.to_csv(index=False), "text/csv")
    print(f"[SUCCESS] 輸出檔案")

    return {"message": "Report generated", "filename": filename}

# -----------------------------------
# GET API: 固定區間
# -----------------------------------
@app.get("/")
def get_jira_report():
    try:
        start_date = "2025-09-01"
        end_date = "2025-10-01"
        return generate_report(start_date, end_date)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# -----------------------------------
# POST API: 自訂區間
# -----------------------------------
@app.post("/jira-report")
def post_jira_report(daterange: DateRange):
    try:
        return generate_report(daterange.start_date, daterange.end_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
