import os
from datetime import datetime
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, validator
import pandas as pd
from jira_api import JiraAPI, GROUPS, project_data_to_df, filter_df_by_date, user_data_to_df
from google.cloud import storage

# 環境變數
domain = os.environ.get("JIRA_DOMAIN")
email = os.environ.get("JIRA_EMAIL")
token = os.environ.get("JIRA_TOKEN")
GCS_BUCKET = os.environ.get("GCS_BUCKET")

if not domain or not email or not token or not GCS_BUCKET:
    raise RuntimeError("Missing required environment variables")

# Jira API 初始化
Jira = JiraAPI(domain, email, token)

# 建立 FastAPI App
app = FastAPI(title="Jira Report API")

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
    issues = Jira.get_active_issues(start_date, end_date)
    projects = Jira.trace_project_info_by_issues(issues)
    user_data = {}
    for project in projects:
        for issue in project["issues"]:
            issue["worklogs"] = Jira.get_worklog_from_issue_id(issue["key"])
            for wl in issue["worklogs"]:
                user_id = wl.get("owner_id")
                if user_id and user_id not in user_data:
                    user_data[user_id] = Jira.get_user_group_info_from_user_id(user_id)

    df = project_data_to_df(projects)
    user_df = user_data_to_df(user_data)
    df = pd.merge(df, user_df, on="worklog_owner_id", how="left")

    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    filtered_df = filter_df_by_date(df, start, end)

    filename = f"jiraReport_{start_date}_{end_date}.csv"
    client = storage.Client()
    bucket = client.bucket(GCS_BUCKET)
    blob = bucket.blob(filename)
    blob.upload_from_string(filtered_df.to_csv(index=False), "text/csv")

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
