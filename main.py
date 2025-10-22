import os
from datetime import datetime
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, validator
import pandas as pd
from jira_api import JiraAPI, GROUPS,project_data_to_df, filter_df_by_date, user_data_to_df
from dotenv import load_dotenv
from google.cloud import storage, pubsub_v1
import uvicorn

load_dotenv()
domain = os.getenv("JIRA_DOMAIN")
email = os.getenv("JIRA_EMAIL")
token = os.getenv("JIRA_TOKEN")

GCP_PROJECT = os.environ.get("GCP_PROJECT")
GCS_BUCKET = os.environ.get("GCS_BUCKET")
PUBSUB_TOPIC = os.environ.get("PUBSUB_TOPIC")

if not all([email, token]):
    raise RuntimeError("JIRA_EMAIL or JIRA_TOKEN not set in .env")

if not GCP_PROJECT:
    raise RuntimeError("GCP_PROJECT not set in .env")

if not GCS_BUCKET or not PUBSUB_TOPIC:
    raise RuntimeError("GCS_BUCKET or PUBSUB_TOPIC not set in .env")

Jira = JiraAPI(domain, email, token)
app = FastAPI(title="Jira Report API")

# === POST Body schema ===
class DateRange(BaseModel):
    start_date: str  # YYYY-MM-DD
    end_date: str    # YYYY-MM-DD

    @validator("start_date", "end_date")
    def check_date_format(cls, v):
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            raise ValueError("Date must be YYYY-MM-DD")
        return v


# -----------------------------------
# GET API: 固定區間，排程呼叫
# -----------------------------------
@app.get("/")
def get_jira_report():
    return {"status": "report generation started"}
    # try:
    #     start_date = "2025-09-01"
    #     end_date = "2025-10-01"
    #     return generate_report(start_date, end_date)
    # except Exception as e:
    #     raise HTTPException(status_code=500, detail=str(e))


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


# -----------------------------------
# 共用報表生成函數
# -----------------------------------
def generate_report(start_date: str, end_date: str):
    # Step 1: 取得 issues
    issues = Jira.get_active_issues(start_date, end_date)

    # Step 2: 轉成 projects 結構
    projects = Jira.trace_project_info_by_issues(issues)

    # Step 3: 補 worklogs 與 user info
    user_data = {}
    for project in projects:
        for issue in project["issues"]:
            issue["worklogs"] = Jira.get_worklog_from_issue_id(issue["key"])
            for wl in issue["worklogs"]:
                user_id = wl.get("owner_id")
                if user_id and user_id not in user_data:
                    user_data[user_id] = Jira.get_user_group_info_from_user_id(user_id)

    # Step 4: 轉 DataFrame
    df = project_data_to_df(projects)
    user_df = user_data_to_df(user_data)
    df = pd.merge(df, user_df, on="worklog_owner_id", how="left")

    # Step 5: 篩選日期
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    filtered_df = filter_df_by_date(df, start, end)

    # Step 6: 儲存到 GCS
    filename = f"jiraReport_{start_date}_{end_date}.csv"
    client = storage.Client()
    bucket = client.bucket(GCS_BUCKET)
    blob = bucket.blob(filename)
    blob.upload_from_string(filtered_df.to_csv(index=False), "text/csv")

    # Step 7: 發送 Pub/Sub
    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(GCP_PROJECT, PUBSUB_TOPIC)
    message = f"Jira report generated: {filename}".encode("utf-8")
    future = publisher.publish(topic_path, message)
    future.result()  # 確保訊息送出

    return {"message": "Report generated", "filename": filename}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
