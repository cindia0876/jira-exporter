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
from io import BytesIO

# 建立 FastAPI App
app = FastAPI()

# jira_api = None
GCS_BUCKET = None

# -----------------------------------
# 從 Secret Manager 取得 secret 值
#      參數：
#          secret_name : projects/{project_id}/secrets/{secret_id}
#          version : latest
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
# 月報表生成函數
# -----------------------------------
def generate_report(start_date: str, end_date: str):
    jira_api = init_jira_api("monthly")
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
            issue["worklogs"] = jira_api.get_worklog_from_issue_id(issue["issues_key"])
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

    print(f"Step 6: 輸出檔案並存入GCS")
    filename = f"jiraReport_{start_date}_{end_date}.csv"
    client = storage.Client()
    bucket = client.bucket(GCS_BUCKET)
    blob = bucket.blob(filename)
    blob.upload_from_string(filtered_df.to_csv(index=False, encoding="utf-8-sig"), content_type="text/csv; charset=utf-8")
    print(f"[SUCCESS] 輸出檔案")

    return {"message": "Report generated", "filename": filename}

# -----------------------------------
# GET API: 每個月自動匯出月報表
# -----------------------------------
@app.get("/reports/monthly/auto")
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

        # 這個月的第一天
        last_day = date(today.year, today.month, 1)

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
@app.get("/reports/monthly")
def post_monthlyReports(start_date: str, end_date: str):
    try:
        return generate_report(start_date, end_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# -----------------------------------
# POST API: 依照「專案」匯出報表
#     參數：
#         project_key (str): JIRA 專案代碼
# -----------------------------------
@app.get("/reports/projects")
def post_reportsByProjects(project_key): 
    jira_api = init_jira_api("project")
    print(f"Fetching information By {project_key}")

    print(f"Step 1: 取得專案基本資訊")
    project = jira_api.get_one_project(project_key)[0]
    project_name = project['project_name']
    project_id = project['project_key']
    print(f"[INFO] 專案名稱：{project_name}, 專案 ID：{project_id}")

    print("Step 2: 取得該專案的所有 Issues")
    issues = jira_api.get_issue_from_project_id(project_id)
    project['issues'] = issues
    print(f"[INFO] 已取得 {len(issues)} 筆 issue")

    print("Step 3: 取得每個 Issue 的 Worklogs")
    worklogs = []
    if 'issues' in project:
        for issue in project['issues']:
            issue_id = issue['issues_key']
            worklogs = jira_api.get_worklog_from_issue_id(issue_id)
            issue['worklogs'] = worklogs
    # print(f"issues:{issues}")
    print(f"[INFO] 所有 Issue 的 Worklogs 已載入完成")

    print("Step 4: 轉換每個 Worklog 的使用者 ID 為群組資訊")
    for issue in project['issues']:
        if 'worklogs' in issue:
            for worklog in issue['worklogs']:
                user_id = worklog['owner_id']
                groups = jira_api.get_user_group_info_from_user_id(user_id)
                worklog['groups'] = groups
    print("[INFO] 使用者群組資訊已附加到每筆 Worklog")

    print("Step 5: 準備轉換資料為 DataFrame 結構")
    # Step 1: 先 normalize project -> issues
    df = pd.json_normalize(project, record_path=['issues'], meta=['project_name', 'project_key', 'project_category'], errors='ignore')
    # Step 2: 將 worklogs explode
    if 'worklogs' in df.columns:
        df = df.explode('worklogs').reset_index(drop=True)
        worklog_df = pd.json_normalize(df['worklogs']).add_prefix('worklog_')
        df = pd.concat([df.drop(columns=['worklogs']), worklog_df], axis=1)
    else:
        df['worklog_owner'] = None
        df['worklog_start_date'] = pd.NaT
        df['worklog_time_spent_hr'] = None

    print("Step 6: [開始] 統計每位 worklog_owner 的總工時")
    if not df.empty:
        df['worklog_month'] = pd.to_datetime(df['worklog_start_date']).dt.strftime('%Y-%m')

        # 建立依月份彙總的樞紐表
        summary_df = (
            df.pivot_table(
                index='worklog_owner',
                columns='worklog_month',
                values='worklog_time_spent_hr',
                aggfunc='sum',
                fill_value=0
            )
            .reset_index()
        )

        # 加上總工時欄位
        summary_df['total_time_spent_hr'] = summary_df.iloc[:, 1:].sum(axis=1)

        # 按總工時排序
        summary_df = summary_df.sort_values(by='total_time_spent_hr', ascending=False)

        print(f"[INFO] Summary_ByMonth 建立完成，共 {len(summary_df)} 位成員")

    else:
        print("[WARN] 無 Worklog 資料，建立空的 Summary_ByMonth")
        summary_df = pd.DataFrame(columns=['worklog_owner', 'total_time_spent_hr'])
    
    print("Step 6: [結束] 統計每位 worklog_owner 的總工時")

    # Step 3: 改欄位名稱 & 移除多餘欄位
    df.rename(columns={
        'worklog_groups.Executive Unit': 'worklog_owner_EU',
        'worklog_groups.Job Level': 'worklog_owner_level',
        'worklog_groups.Job Title': 'worklog_owner_title',
    }, inplace=True)

    columns_to_drop = [
        'worklog_groups.user_id',
        'worklog_owner_id',
        'worklog_month'
    ]
    df = df.drop(columns=[col for col in columns_to_drop if col in df.columns])

    # Step 4: 確保 worklog_start_date 是 datetime
    if 'worklog_start_date' in df.columns:
        df['worklog_start_date'] = pd.to_datetime(df['worklog_start_date'], errors='coerce').dt.date
    else:
        df['worklog_start_date'] = pd.NaT

    # Step 5: 將 Parent_Key 與 Worklog_Type 移到最後
    project_cols = [c for c in df.columns if c.startswith('project_')]
    other_cols = [c for c in df.columns if c not in project_cols + ['Parent_Key', 'Worklog_Type']]
    final_cols = project_cols + other_cols + ['Parent_Key', 'Worklog_Type']
    df_final = df[[c for c in final_cols if c in df.columns]] 

    print("Step 7: 輸出檔案並存入GCS")
    filename = f"jiraReport_{project_name}.xlsx"
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df_final.to_excel(writer, sheet_name="Worklogs_Detail", index=False)
        summary_df.to_excel(writer, sheet_name="Worklogs_Summary", index=False)
    client = storage.Client()
    bucket = client.bucket(GCS_BUCKET)
    blob = bucket.blob(filename)
    blob.upload_from_string(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    print(f"[SUCCESS] 輸出檔案")
    return {"message": "Report generated", "filename": filename}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
