import os
from datetime import datetime
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, validator
import pandas as pd
from jira_api_monthly_report import JiraMonthlyAPI, GROUPS, project_data_to_df, filter_df_by_date, user_data_to_df
from jira_api_project_report import JiraProjectAPI
from google.cloud import storage
from google.cloud import secretmanager
from datetime import date, datetime
import calendar

# 建立 FastAPI App
app = FastAPI()

# jira_api = None
GCS_BUCKET = None

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

# -----------------------------------
# JIRA API & 初始化
# -----------------------------------
jira_apis = {}
def init_jira_api(api_type: str):
    global jira_apis, GCS_BUCKET
    if api_type in jira_apis:
        return jira_apis[api_type]

    domain = os.environ.get("JIRA_DOMAIN")
    print(f"[INFO] domain :{domain} ")
    if not domain:
        raise RuntimeError("Missing environment variable: JIRA_DOMAIN")

    GCS_BUCKET = os.environ.get("GCS_BUCKET")
    print(f"[INFO] GCS_BUCKET :{GCS_BUCKET} ")
    if not GCS_BUCKET:
        raise RuntimeError("Missing environment variable: GCS_BUCKET")

    project_id = os.environ.get("GCP_PROJECT_NUM")
    print(f"[INFO] project_id :{project_id} ")
    if not project_id:
        raise RuntimeError("Missing environment variable: GCP_PROJECT_NUM")

    email_secret = os.environ.get("JIRA_EMAIL_SECRET_NAME")
    print(f"[INFO] email_secret :{email_secret} ")
    token_secret = os.environ.get("JIRA_TOKEN_SECRET_NAME")
    print(f"[INFO] token_secret :{token_secret} ")
    if not email_secret or not token_secret:
        raise RuntimeError("Missing Jira secret names in environment variables")

    jira_email = access_secret(f"projects/{project_id}/secrets/{email_secret}")
    print(f"[INFO] jira_email :{jira_email} ")
    jira_token = access_secret(f"projects/{project_id}/secrets/{token_secret}")
    print(f"[INFO] jira_token :{jira_token} ")

    # 動態建立不同的 Jira API 類別
    if api_type == "monthly":
        api_instance = JiraMonthlyAPI(domain, jira_email, jira_token)
    elif api_type == "project":
        api_instance = JiraProjectAPI(domain, jira_email, jira_token)
    else:
        raise ValueError(f"Unknown api_type: {api_type}")

    jira_apis[api_type] = api_instance
    print(f"[INFO] Jira API initialized for type: {api_type}")
    return api_instance

# -----------------------------------
# 共用報表生成函數
# -----------------------------------
def generate_report(start_date: str, end_date: str):
    jira_api = get_jira_api("monthly")
    print(f"Fetching issues from {start_date} to {end_date}")

    print(f"Step 1: 取得 issues")
    issues = jira_api.get_active_issues(start_date, end_date)
    print(f"[INFO] 總共取得 {len(issues)} 筆 active issues")

    print(f"Step 2: issues 轉成 projects 結構")
    projects = jira_api.trace_project_info_by_issues(issues)
    print(f"[INFO] 對應到 {len(projects)} 個 project")

    
    print(f"Step 3: 逐一補上每個 issue 的 worklogs 與 user info")
    user_data = {}
    for project in projects:
        for issue in project["issues"]:
            issue["worklogs"] = jira_api.get_worklog_from_issue_id(issue["key"])
            for wl in issue["worklogs"]:
                user_id = wl.get("owner_id")
                if user_id and user_id not in user_data:
                    user_data[user_id] = jira_api.get_user_group_info_from_user_id(user_id)

    print(f"Step 4: 轉換為 DataFrame")
    df = project_data_to_df(projects)
    user_df = user_data_to_df(user_data)
    df = pd.merge(df, user_df, on="worklog_owner_id", how="left")
    print(f"[INFO] 最終資料筆數含 worklogs：{len(df)}")

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
    blob.upload_from_string(filtered_df.to_csv(index=False, encoding="utf-8-sig"), content_type="text/csv; charset=utf-8")
    print(f"[SUCCESS] 輸出檔案")

    return {"message": "Report generated", "filename": filename}

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
# GET API: 每個月自動匯出月報表
# -----------------------------------
@app.get("/")
# @app.get("/reports/monthly/auto")
def get_monthlyReportsAuto():
    try:
        # 今天
        today = date.today()

        # 上個月的年份和月份
        year = today.year
        month = today.month - 1
        if month == 0:  # 如果今天是 1 月，上一個月是去年 12 月
            month = 12
            year -= 1

        # 上個月的第一天
        first_day = date(year, month, 1)

        # 上個月的最後一天
        last_day = date(year, month, calendar.monthrange(year, month)[1])

        start_date = first_day.strftime("%Y-%m-%d")
        end_date = last_day.strftime("%Y-%m-%d")
        
        return generate_report(start_date, end_date)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# -----------------------------------
# POST API: 自訂匯出報表的時間區間
#     參數：
#         start_date (str): 起始日期(如：2025-09-01)
#         end_date (str): 結束日期(如：2025-09-01)
# -----------------------------------
@app.post("/reports/monthly")
def post_monthlyReports(daterange: DateRange):
    try:
        return generate_report(daterange.start_date, daterange.end_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# -----------------------------------
# POST API: 依照「專案」匯出報表
#     參數：
#         project_key (str): JIRA 專案代碼
# -----------------------------------
@app.post("/reports/projects")
def post_reportsByProjects(roject_key): 
    jira_api = get_jira_api()
    print(f"Fetching information By {projects}")

    print(f"Step 1: 取得專案基本資訊 (project_key={project_key})")
    project = jira_api.get_one_project(project_key)[0]
    project_name = project['project_name']
    project_id = project['project_id']
    print(f"✅ 專案名稱：{project_name}, 專案 ID：{project_id}")

    print("Step 2: 取得該專案的所有 Issues")
    issues = jira_api.get_issue_from_project_id(project_id)
    project['issues'] = issues
    print(f"✅ 已取得 {len(issues)} 筆 issue")

    print("Step 3: 取得每個 Issue 的 Worklogs")
    worklogs = []
    if 'issues' in project:
        for issue in project['issues']:
            issue_id = issue['key']
            worklogs = jira_api.get_worklog_from_issue_id(issue_id)
            issue['worklogs'] = worklogs
        print(f"✅ 所有 Issue 的 Worklogs 已載入完成")

    print("Step 4: 轉換每個 Worklog 的使用者 ID 為群組資訊")
    for issue in project['issues']:
        if 'worklogs' in issue:
            for worklog in issue['worklogs']:
                user_id = worklog['owner_id']
                groups = jira_api.get_user_group_info_from_user_id(user_id)
                worklog['groups'] = groups
    print("✅ 使用者群組資訊已附加到每筆 Worklog")

    print("Step 5: 準備轉換資料為 DataFrame 結構")
    expected_columns = [
        'project_key', 'project_name', 'project_category', 'issues',
        'issues_name', 'issues_key', 'issues_assignee', 'issues_team', 'issues_status',
        'worklog_owner_id', 'worklog_owner', 'worklog_time_spent_hr', 'worklog_start_date',
        'worklog_comment', 'worklog_owner_EU', 'worklog_owner_level', 'worklog_owner_title'
    ]

    df = pd.DataFrame([project])
    df_issues_exploded = df.explode("issues").reset_index(drop=True)
    print("✅ 專案資料展開完成")

    print("Step 6: 正規化 Issue 與 Worklog 結構")
    if not df_issues_exploded['issues'].isnull().all():
        df_issues_normalized = pd.json_normalize(df_issues_exploded.to_dict(orient="records"))

        if 'issues.worklogs' in df_issues_normalized.columns:
            df_worklogs_exploded = df_issues_normalized.explode("issues.worklogs").reset_index(drop=True)
            df_final = pd.json_normalize(df_worklogs_exploded.to_dict(orient="records"))
            print("✅ Worklogs 欄位展開完成")

            print("Step 7: 重新命名欄位並清理資料")
            df_final = df_final.rename(columns={
                'project_id': 'project_key',
                'issues.worklogs.owner_id': 'worklog_owner_id',
                'issues.worklogs.owner': 'worklog_owner',
                'issues.worklogs.time_spent_hr': 'worklog_time_spent_hr',
                'issues.worklogs.start_date': 'worklog_start_date',
                'issues.worklogs.comment': 'worklog_comment',
                'issues.worklogs.groups.Executive Unit': 'worklog_owner_EU',
                'issues.worklogs.groups.Job Level': 'worklog_owner_level',
                'issues.worklogs.groups.Job Title': 'worklog_owner_title',
                'issues.name': 'issues_name',
                'issues.key': 'issues_key',
                'issues.assignee': 'issues_assignee',
                'issues.team': 'issues_team',
                'issues.status': 'issues_status'
            })

            # 移除不必要欄位
            columns_to_drop = [col for col in ['issues.worklogs', 'issues'] if col in df_final.columns]
            df_final = df_final.drop(columns_to_drop, axis=1, errors='ignore')

            # 計算整個專案的總工時
            total_time = df_final['worklog_time_spent_hr'].sum()
            total_time = round(total_time, 1)
            df_final.insert(0, 'total_time_spent', total_time)
            print(f"✅ 專案總工時計算完成：{total_time} 小時")

        else:
            print("⚠️ 此專案沒有任何 Worklogs，建立空的 DataFrame")
            df_final = pd.DataFrame(columns=expected_columns)
            if not df_issues_normalized.empty:
                df_final['project_key'] = df_issues_normalized['project_id']
                df_final['project_name'] = df_issues_normalized['project_name']
                df_final['project_category'] = df_issues_normalized['project_category']
            df_final.insert(0, 'total_time_spent', 0.0)

    else:
        print("⚠️ 專案中沒有任何 Issues，建立空的 DataFrame")
        df_final = pd.DataFrame(columns=expected_columns)
        df_final.insert(0, 'total_time_spent', 0.0)

    print("Step 8: 輸出 CSV 檔案")
    filename = f"{project_name}.csv"
    output_path = f"{output_dir}/{filename}"
    df_final.to_csv(output_path, index=False)
    print(f"✅ 專案資料已匯出完成：{output_path}")

    return df_final


    

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
