import os
from datetime import datetime
from flask import Flask, request, jsonify
import pandas as pd
from jira_api import JiraAPI, GROUPS, project_data_to_df, filter_df_by_date, user_data_to_df
from dotenv import load_dotenv
from google.cloud import storage

load_dotenv()

# 取得環境變數
domain = os.getenv("JIRA_DOMAIN")
email = os.getenv("JIRA_EMAIL")
token = os.getenv("JIRA_TOKEN")
GCS_BUCKET = os.getenv("GCS_BUCKET")

if not domain or not email or not token or not GCS_BUCKET:
    raise RuntimeError("Missing required environment variables")

# Jira API 初始化
Jira = JiraAPI(domain, email, token)

# 建立 Flask App
app = Flask(__name__)

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
@app.route("/", methods=["GET"])
def get_jira_report():
    try:
        start_date = "2025-09-01"
        end_date = "2025-10-01"
        result = generate_report(start_date, end_date)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# -----------------------------------
# POST API: 自訂區間
# -----------------------------------
@app.route("/jira-report", methods=["POST"])
def post_jira_report():
    try:
        data = request.get_json()
        start_date = data.get("start_date")
        end_date = data.get("end_date")
        for d in [start_date, end_date]:
            try:
                datetime.strptime(d, "%Y-%m-%d")
            except Exception:
                return jsonify({"error": "Date must be YYYY-MM-DD"}), 400
        result = generate_report(start_date, end_date)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# -----------------------------------
# 啟動 Flask
# -----------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
