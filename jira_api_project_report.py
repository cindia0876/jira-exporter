import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime
import logging
import dateutil.parser
from dateutil.parser import isoparse
import pandas as pd

GROUPS = {
    "Executive Unit": [
        "AWS-TW",
        "AWS-HK",
        "GCP-TW",
        "GWS-TW"
        "Google-HK",
        "Data",
        "Multicloud",
        "MS",
        "PMO"
        "SEA"
    ],
    "Job Level": [
        "L1",
        "L2",
        "L3"
    ],
    "Job Title": [
        "SA",
        "PM",
        "Data Engineer",
        "SRE",
        "TAM"
    ]
}

class JiraProjectAPI:
    """
    This class is used to interact with Jira API.
    A environment file is required to store the email and token.
    """

    def __init__(self, domain, email, token) -> None:
        self.domain = domain
        self.email = email
        self.token = token
        self.header = {"Accept": "application/json"}
        self.auth = HTTPBasicAuth(email, token)


    # GET PROJECT NAME
    def get_one_project(self, key: str,raw: bool = False,) -> list[dict]:

        url = f"{self.domain}/rest/api/3/project/{key}"
        response = requests.get(url, headers=self.header, auth=self.auth)
        data = response.json()
        if raw:
            return data
        parsed_list = []
        parsed = {}
        parsed["project_name"] = data.get("name")
        parsed["project_id"] = data.get("key")
        if data.get("projectCategory"):
            parsed["project_category"] = data.get("projectCategory")["name"]
        else:
            parsed["project_category"] = None
        parsed_list.append(parsed)
        return parsed_list

    # GET ISSUE

    def get_issue_from_project_id(
        self,
        project_id: str,
        max_results: int = 50,
        start_at: int = 0,
        raw: bool = False
    ) -> list[dict]:
        """
        Get all issues from a given Jira project (with pagination support).
        """
        print(f"[INFO] 開始取得專案 {project_id} 的 Issues（含分頁）")
        issues = []
        next_page_token = None
        while True:
            # Step 1️⃣ 組合查詢參數
            query = {
                "jql": f'project="{project_id}" ORDER BY created ASC, key ASC',
                "fields": "summary,assignee,customfield_10001,customfield_10039",
                "maxResults": max_results,
                "startAt": start_at,
            }
            if next_page_token:
                query["nextPageToken"] = next_page_token

            url = f"{self.domain}/rest/api/3/search/jql"

            # Step 2️⃣ 發送請求
            response = requests.get(url, headers=self.header, auth=self.auth, params=query)
            if response.status_code != 200:
                print(f"[ERROR] /search/jql：issues獲取失敗 ({response.status_code})")
                raise PermissionError(response.text)

            data = response.json()
            next_page_token = data.get("nextPageToken")
            print(f"[DEBUG] next_page_token: {next_page_token}")

            # Step 3️⃣ 若使用 raw 模式，直接返回原始 JSON
            if raw:
                issues.extend(data.get("issues", []))
            else:
                parsed_list = []
                print(f"[INFO] 開始解析 Issues（目前 startAt={start_at}）")

                for issue in data.get("issues", []):
                    parsed = {}
                    parsed["name"] = issue["fields"].get("summary")
                    parsed["key"] = issue.get("key")

                    if issue["fields"].get("assignee"):
                        parsed["assignee"] = issue["fields"]["assignee"]["displayName"]
                    else:
                        parsed["assignee"] = None

                    if issue["fields"].get("customfield_10001"):
                        parsed["team"] = issue["fields"]["customfield_10001"]["name"]
                    else:
                        parsed["team"] = None

                    if issue["fields"].get("customfield_10039"):
                        parsed["status"] = issue["fields"]["customfield_10039"]["value"]
                    else:
                        parsed["status"] = None

                    parsed_list.append(parsed)

                issues.extend(parsed_list)
                print(f"[INFO] 結束解析 Issues，本頁共 {len(parsed_list)} 筆")

            # Step 4️⃣ 檢查是否有下一頁
            if not next_page_token:
                print(f"[INFO] 已到最後一頁，結束分頁查詢")
                break

            start_at += max_results
        return issues



    # worklog_owner, worklog_owner_id	worklog_start_date	worklog_time_spent_hr	worklog_comment


    global issue_id
    def get_worklog_from_issue_id(self, issue_id: str, raw: bool = False) -> list[dict]:
        url = f"{self.domain}/rest/api/2/issue/{issue_id}/worklog"
        response = requests.get(url, headers=self.header, auth=self.auth)
        data = response.json()
        if raw:
            return data
        worklogs: list[dict] = data["worklogs"]
        parsed_list = []
        for worklog in worklogs:
            parsed = {}
            if "author" in worklog:
                parsed["owner"] = worklog["author"]["displayName"]
                parsed["owner_id"] = worklog["author"]["accountId"]
            else:
                parsed["owner"] = None
                parsed["owner_id"] = None
            parsed["start_date"] = datetime.strptime(
                worklog["started"], "%Y-%m-%dT%H:%M:%S.%f%z"
            ).date()
            parsed["time_spent_hr"] = worklog["timeSpentSeconds"] / 3600
            parsed["comment"] = worklog.get("comment")
            parsed_list.append(parsed)
        return parsed_list

    def get_user_group_info_from_user_id(self, user_id: str, raw: bool = False) -> dict:


            url = f"{self.domain}/rest/api/2/user"

            query = {"accountId": user_id, "expand": "groups,applicationRoles"}
            response = requests.get(url, headers=self.header, params=query, auth=self.auth)
            data = response.json()
            if raw:
                return data

            user_labels = {"user_id": user_id}
            # groups = load_groups()

            if "groups" in data and "items" in data["groups"]:
                user_groups = [item["name"] for item in data["groups"]["items"]]
                for category, names in GROUPS.items():
                    for name in names:
                        if name in user_groups:
                            user_labels[category] = name
            else:
                logging.warning(f"No groups found for user ID: {user_id}")
                user_labels["groups"] = None

            return user_labels



def process_worklogs(issue, user_data, Jira):
    for worklog in issue["worklogs"]:
        if worklog["owner_id"] not in user_data:
            user_info = Jira.get_user_group_info_from_user_id(worklog["owner_id"])
            logging.info(f"User info: {user_info}")
            user_data[worklog["owner_id"]] = user_info

def process_issues(project, user_data, Jira):
    for issue in project["issues"]:
        issue["worklogs"] = Jira.get_worklog_from_issue_id(issue["key"])
        if issue["worklogs"]:
            process_worklogs(issue, user_data, Jira)

def process_projects(projects, user_data, Jira):
    for project in projects:
        logging.info(f"Processing project: {project['project_key']}")
        project["issues"] = Jira.get_issue_from_project_id(project["project_key"])
        if project["issues"]:
            process_issues(project, user_data, Jira)