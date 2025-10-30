import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime
import logging
import dateutil.parser
from dateutil.parser import isoparse
import pandas as pd


GROUPS = {
    "Executive Unit": [
        "AWS-TW","AWS-HK","GCP-TW","GWS-TW","Google-HK",
        "Data","Multicloud","MS","PMO","專案開發部","SEA","產品及解決方案處"
    ],
    "Job Level": ["TWO1","TWO2","TWO3","HKO1"],
    "Job Title": ["SA","PM","Data Engineer","SRE","TAM"]
}

class JiraMonthlyAPI:

    def __init__(self, domain, email, token) -> None:
        self.domain = domain
        self.email = email
        self.token = token
        self.header = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
        self.auth = HTTPBasicAuth(email, token)

    def get_all_projects(self, raw: bool = False) -> list[dict]:
        url = f"{self.domain}/rest/api/3/project"
        response = requests.get(url, headers=self.header, auth=self.auth)
        data = response.json()
        if raw:
            return data
        projects: list[dict] = data["values"]
        parsed_list = []
        for project in projects:
            parsed = {}
            parsed["project_name"] = project.get("name")
            parsed["project_key"] = project.get("key")
            if project.get("projectCategory"):
                parsed["project_category"] = project.get("projectCategory")["name"]
            else:
                parsed["project_category"] = None
            parsed_list.append(parsed)
        return parsed_list

    def get_issue_from_project_id(
        self, project_id: str, raw: bool = False
    ) -> list[dict]:

        url = f"{self.domain}/rest/api/2/search"
        query = {"jql": f'project= "{project_id}"'}
        response = requests.get(url, headers=self.header, params=query, auth=self.auth)
        data = response.json()
        if raw:
            return data
        if data.get("issues") is None:
            return []
        issues: list[dict] = data["issues"]
        parsed_list = []
        for issue in issues:
            parsed = {}
            parsed["name"] = issue["fields"].get("summary")
            parsed["key"] = issue.get("key")
            if issue["fields"].get("customfield_10001"):
                parsed["team"] = issue["fields"]["customfield_10001"]["name"]
            else:
                parsed["status"] = None
            if issue["fields"].get("customfield_10035"):
                parsed["status"] = issue["fields"]["customfield_10035"]["value"]
            else:
                parsed["status"] = None
            parsed_list.append(parsed)
        return parsed_list

    def get_worklog_from_issue_id(self, issue_id: str, raw: bool = False) -> list[dict]:
          worklogs = []
          start_at = 0
          max_results = 100
          while True:
              url = f"{self.domain}/rest/api/3/issue/{issue_id}/worklog"
              query = {"startAt": start_at, "maxResults": max_results}
              response = requests.get(url, headers=self.header, auth=self.auth, params=query)
              if response.status_code != 200:
                  print(f"[ERROR] /issue/{issue_id}/worklog：獲取失敗 ({response.status_code})")
                  break
              data = response.json()

              batch = data.get("worklogs", [])
              for worklog in batch:
                  parsed = {
                      "owner": worklog.get("author", {}).get("displayName"),
                      "owner_id": worklog.get("author", {}).get("accountId"),
                      "start_date": isoparse(worklog["started"]).date(),
                      "time_spent_hr": worklog["timeSpentSeconds"] / 3600
                  }
                  worklogs.append(parsed)

              # 分頁判斷邏輯
              if len(batch) < max_results:
                  break
              start_at += max_results

          return worklogs

    def get_user_group_info_from_user_id(self, user_id: str, raw: bool = False) -> dict:
        """
        Get user group information from user ID.
        The method extracts the user ID, executive unit, job level and job title.
        The raw parameter can be set to True to return the raw json data.
        Returns a dictionary.
        """
        url = f"{self.domain}/rest/api/3/user"

        query = {"accountId": user_id, "expand": "groups,applicationRoles"}
        response = requests.get(url, headers=self.header, params=query, auth=self.auth)
        data = response.json()
        if raw:
            return data

        user_labels = {"user_id": user_id}
        groups = GROUPS

        if "groups" in data and "items" in data["groups"]:
            user_groups = [item["name"] for item in data["groups"]["items"]]
            for category, names in groups.items():
                for name in names:
                    if name in user_groups:
                        user_labels[category] = name
        else:
            logging.warning(f"No groups found for user ID: {user_id}")
            user_labels["groups"] = None

        return user_labels
   
    # ---------Extended functioanlities to get active issues ----------------



    def get_active_issues(
        self,
        start_date: str,
        end_date: str,
        max_results: int = 50,
        start_at: int = 0,
        raw: bool = False,
    ) -> list[dict]:
        """
        Get all active issues from Jira.
        Pagination considered.
        """
        issues = []
        next_page_token = None
        while True:
            query = {
                "jql": f""" worklogDate >= "{start_date}" AND worklogDate < "{end_date}" ORDER BY created ASC, key ASC """,
                "fields": "summary,project,worklog,customfield_10001,customfield_10035,customfield_10142,customfield_10139",
                "maxResults": max_results,
                "startAt": start_at,
            }
            if next_page_token:
                query["nextPageToken"] = next_page_token

            url = f"{self.domain}/rest/api/3/search/jql"
            response = requests.get(url, headers=self.header, auth=self.auth, params=query)

            if response.status_code != 200:
                print(f"[ERROR] /search/jql：issues獲取失敗 ({response.status_code})")
                raise PermissionError(response.text)

            data = response.json()
            next_page_token = data.get("nextPageToken")
            print(f"[DEBUG] next_page_token:{next_page_token}")

            if raw:
                issues.extend(data["issues"])
            else:
                parsed_list = []
                print(f"[INFO] 開始解析issues")
                for issue in data["issues"]:
                    parsed = {}
                    parsed["issues_name"] = issue["fields"].get("summary")
                    parsed["issues_key"] = issue.get("key")
                    parsed["project_key"] = issue["fields"]["project"]["key"]
                    if issue["fields"].get("customfield_10001"):
                        parsed["issues_team"] = issue["fields"]["customfield_10001"]["name"]
                    else:
                        parsed["issues_team"] = None

                    if issue["fields"].get("customfield_10035"):
                        parsed["issues_status"] = issue["fields"]["customfield_10035"]["value"]
                    else:
                        parsed["issues_status"] = None

                    # 抓取客製化欄位 10142 和 10139 的值
                    parsed["customfield_10142"] = issue["fields"].get("customfield_10142")
                    parsed["customfield_10139"] = safe_get_value(issue["fields"], "customfield_10139")
                    parsed_list.append(parsed)
                issues.extend(parsed_list)
                print(f"[INFO] 結束解析issues")
           
            # 分頁判斷邏輯
            next_page_token = data.get("nextPageToken")
            if not next_page_token:
                break

        return issues

    def get_project_info_by_key(self, project_key: str, raw: bool = False) -> dict:
        """
        Get project information by project key.
        """
        url = f"{self.domain}/rest/api/2/project/{project_key}"
        response = requests.get(url, headers=self.header, auth=self.auth)
        data = response.json()
        if raw:
            return data
        project = {}
        project["project_name"] = data.get("name")
        project["project_key"] = data.get("key")
        if data.get("projectCategory"):
            project["project_category"] = data.get("projectCategory")["name"]
        else:
            project["project_category"] = None
        return project

    def trace_project_info_by_issues(self, issues: list[dict]) -> list[dict]:
        """
        Get project information by issues.
        """
        # group issues by projects into dictionary
        project_grouping = {}
        print(f"[INFO] 開始組合issues的project information")
        for issue in issues:
            project_key = issue["project_key"]
            if project_key not in project_grouping:
                project_grouping[project_key] = []
            # pop project_key from issue
            issue.pop("project_key")
            project_grouping[project_key].append(issue)

        projects = []
        for project_key in project_grouping:
            project = self.get_project_info_by_key(project_key)
            project["issues"] = project_grouping[project_key]
            projects.append(project)
        return projects

    def get_worklogs_by_date_range(
        self, start_date: str, end_date: str
    ) -> list[dict]:
        """
        取得指定區間內的所有 worklog (使用 worklog/updated API)
        調整重點：
        1️⃣ 使用 /worklog/updated?since=start_date
        2️⃣ 分頁抓所有 worklog IDs
        3️⃣ 逐筆抓詳細資料
        4️⃣ 篩選出 start_date <= worklog['started'] < end_date
        """
        worklogs_all = []
        since_timestamp = start_date + "T00:00:00.000+0000"
        next_page = None

        while True:
            # ------------------ Step 1: GET /worklog/updated ------------------
            url = f"{self.domain}/rest/api/3/worklog/updated"
            params = {"since": since_timestamp}
            if next_page:
                url = next_page
                params = None  # nextPage 已包含 query
            response = requests.get(url, headers=self.header, auth=self.auth, params=params)
            if response.status_code != 200:
                logging.warning(f"Failed to fetch updated worklogs: {response.text}")
                break
            data = response.json()

            # ------------------ Step 2: 遍歷 worklog IDs ------------------
            for w in data.get("values", []):
                worklog_id = w["worklogId"]
                issue_id = w["issueId"]

                # ------------------ Step 3: 逐筆 GET /issue/{issueId}/worklog/{worklogId} ------------------
                wl_url = f"{self.domain}/rest/api/3/issue/{issue_id}/worklog/{worklog_id}"
                wl_resp = requests.get(wl_url, headers=self.header, auth=self.auth)
                if wl_resp.status_code != 200:
                    logging.warning(f"Failed to fetch worklog {worklog_id}: {wl_resp.text}")
                    continue
                wl_data = wl_resp.json()

                # ------------------ Step 4: 篩選時間區間 ------------------
                started = dateutil.parser.isoparse(wl_data["started"]).date()
                start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
                end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
                if start_dt <= started < end_dt:
                    parsed = {
                        "issue_id": issue_id,
                        "worklog_id": worklog_id,
                        "owner": wl_data.get("author", {}).get("displayName"),
                        "owner_id": wl_data.get("author", {}).get("accountId"),
                        "start_date": started,
                        "time_spent_hr": wl_data.get("timeSpentSeconds", 0) / 3600,
                    }
                    worklogs_all.append(parsed)

            # ------------------ Step 5: 分頁 ------------------
            next_page = data.get("nextPage")
            if not next_page:
                break

        return worklogs_all

def project_data_to_df(projects) -> pd.DataFrame:
    """
    將 Jira project + issues + worklogs 轉成 DataFrame。
    自動整理欄位，生成 worklog_start_date 方便日期篩選。
    """
    if not projects:
        return pd.DataFrame()  # 空 list 回傳空 DataFrame

    # Step 1: 先 normalize project -> issues
    df = pd.json_normalize(projects, record_path=['issues'], meta=['project_name', 'project_key', 'project_category'], errors='ignore')
    
    # Step 2: 將 worklogs explode
    if 'worklogs' in df.columns:
        df = df.explode('worklogs').reset_index(drop=True)
        worklog_df = pd.json_normalize(df['worklogs']).add_prefix('worklog_')
        df = pd.concat([df.drop(columns=['worklogs']), worklog_df], axis=1)
    else:
        df['worklog_owner_id'] = None
        df['worklog_owner'] = None
        df['worklog_start_date'] = pd.NaT
        df['worklog_time_spent_hr'] = None

    # Step 3: 改欄位名稱
    df.rename(columns={
        'customfield_10142': 'Parent_Key',
        'customfield_10139': 'Worklog_Type',
        'worklog_start_date': 'worklog_start_date',
        'worklog_owner': 'worklog_owner',
        'worklog_owner_id': 'worklog_owner_id',
        'worklog_time_spent_hr': 'worklog_time_spent_hr'
    }, inplace=True)

    # Step 4: 確保 worklog_start_date 是 datetime
    if 'worklog_start_date' in df.columns:
        df['worklog_start_date'] = pd.to_datetime(df['worklog_start_date'], errors='coerce').dt.date
    else:
        df['worklog_start_date'] = pd.NaT

    # Step 5: 將 Parent_Key 與 Worklog_Type 移到最後
    project_cols = [c for c in df.columns if c.startswith('project_')]
    other_cols = [c for c in df.columns if c not in project_cols + ['Parent_Key', 'Worklog_Type']]
    final_cols = project_cols + other_cols + ['Parent_Key', 'Worklog_Type']
    df = df[[c for c in final_cols if c in df.columns]]  # 避免 KeyError
    # other_cols = [c for c in df.columns if c not in ['Parent_Key', 'Worklog_Type']]
    # df = df[other_cols + ['Parent_Key', 'Worklog_Type']]

    return df

def filter_df_by_date(df, lower_bound: datetime.date, upper_bound: datetime.date) -> pd.DataFrame:
    """
    Filter the DataFrame by date.
    Includes lower_bound, excludes upper_bound
    """
    if "worklog_start_date" not in df.columns:
        # 如果不存在，檢查原本的 worklog_start_date 欄位是否存在
        if "worklog_start_date" in df.columns:
            df["worklog_start_date"] = df["worklog_start_date"]
        else:
            df["worklog_start_date"] = pd.NaT

    df = df.dropna(subset=["worklog_start_date"])
    filtered_df = df[
        (df["worklog_start_date"] >= lower_bound)
        & (df["worklog_start_date"] < upper_bound)
    ]
    return filtered_df


def user_data_to_df(user_data: list[dict]) -> pd.DataFrame:
    """
    Formats the user data from dictionary to pandas DataFrame.
    """
    user_data = list(user_data.values())
    user_df = pd.json_normalize(user_data)
    user_df.rename(
        {
            "user_id": "worklog_owner_id",
            "Executive Unit": "worklog_owner_EU",
            "Job Level": "worklog_owner_level",
            "Job Title": "worklog_owner_title",
        },
        axis=1,
        inplace=True,
    )
    return user_df

def safe_get_value(field_dict, key):
    value = field_dict.get(key)
    if isinstance(value, dict):
        return value.get("value")
    return None