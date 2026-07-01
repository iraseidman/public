from bs4 import BeautifulSoup
import os
from pathlib import Path
import pandas as pd
import requests
import feedparser
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import pickle
import datetime
import pytz
import imaplib
import email
from email.utils import parsedate_to_datetime
from email.header import decode_header
import re
import yfinance as yf
import csv
from concurrent.futures import ThreadPoolExecutor
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
import subprocess
import json

print("Dashboard script running...")

# pull latest updates from GitHub
subprocess.run(["git", "pull"])

# FUNCTIONS
email_path = Path("iraseidman.github.io-main/client/public/Email")

def get_service():
    creds = None
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)
    service = build('gmail', 'v1', credentials=creds)
    return service

# Save
def save_email_data(email_data, filepath: str):
    df = pd.DataFrame(email_data)
    df.to_csv(filepath, index=False)

    df.to_csv(filepath, index=False, quoting=csv.QUOTE_ALL)


# Function to convert email date to ISO 8601 UTC format (for frontend local time conversion)
def convert_to_utc_iso(date_str):
    try:
        # Parse the date string to datetime
        utc_dt = parsedate_to_datetime(date_str)
        # Convert to UTC and format as ISO 8601
        utc_dt = utc_dt.astimezone(pytz.UTC)
        return utc_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    except Exception as e:
        print(f"Error converting date: {e}")
        return date_str  # Return original date string if parsing or conversion fails


def get_emails(service):
    eastern = pytz.timezone('US/Eastern')

    # Get the current time in EST
    now_eastern = datetime.datetime.now(eastern)

    # Calculate the date one week ago in EST
    one_week_ago = (now_eastern -
                    datetime.timedelta(days=7)).strftime("%Y/%m/%d")

    # Modify the API query to include the lookback period
    results = service.users().messages().list(
        userId='me',
        q=f"after:{one_week_ago}",
        maxResults=10  # add this
    ).execute()
    messages = results.get('messages', [])

    email_data = []

    if not messages:
        print("No messages found.")
    else:
        for message in messages:
            msg = service.users().messages().get(userId='me',
                                                 id=message['id']).execute()

            # Check if the email is unread
            is_unread = 'UNREAD' in msg.get('labelIds', [])

            # Construct the URL to the email
            email_url = f"https://mail.google.com/mail/u/0/#inbox/{msg['id']}"

            # Extracting data from each message
            email_dict = {
                'Preview': msg.get('snippet'),
                'Subject': '',
                'From': '',
                'Date': '',
                'Is Unread':
                is_unread,  # New field indicating if the email is unread
                'Email Link': email_url  # New field for email hyperlink
            }

            payload = msg.get('payload', {})
            headers = payload.get('headers', [])

            for header in headers:
                name = header.get('name')
                value = header.get('value')

                if name == 'Subject':
                    email_dict['Subject'] = value
                if name == 'From':
                    email_dict['From'] = value
                if name == 'Date':
                    email_dict['Date'] = convert_to_utc_iso(value)

            email_data.append(email_dict)

    return email_data

def ensure_folder(folder_name: str) -> str:
    folder_path = os.path.join(os.path.dirname(__file__), folder_name)
    os.makedirs(folder_path, exist_ok=True)
    return folder_path

def git_commit_and_push(repo_path: str, commit_message: str):
    try:
        # Ensure we're in the repo directory
        subprocess.run(
            ["git", "-C", repo_path, "status"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        # Stage all changes
        subprocess.run(
            ["git", "-C", repo_path, "add", "."],
            check=True
        )

        # Commit (will fail gracefully if no changes)
        commit_result = subprocess.run(
            ["git", "-C", repo_path, "commit", "-m", commit_message],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        if "nothing to commit" in commit_result.stdout.lower():
            print("No Git changes to commit.")
            return

        # Push to origin (default branch)
        subprocess.run(
            ["git", "-C", repo_path, "push"],
            check=True
        )

        print("Git commit and push successful.")

    except subprocess.CalledProcessError as e:
        print("Git operation failed:")
        print(e.stderr)

email_folder = ensure_folder("iraseidman.github.io-main/client/public/Email")

# URL of The New Yorker's Daily Cartoon page
session = requests.Session()  #used for web-scraping fetches throughout the entire script

# FETCH EMAIL

# Gmail
try:
    service = get_service()
    gmail_data = get_emails(service)
    gmail_filename = os.path.join(email_folder, "gmail.csv")
    save_email_data(gmail_data, os.path.expanduser(gmail_filename))
    print("Gmail emails saved to email folder")
except Exception as e:
    print("Gmail email access failed - skipping Gmail data")

session.close()  #finish web-scarping so close the session

# OPEN DASHBOARD
# Specify the path to your Excel application and Excel file
excel_path = "/Applications/Microsoft Excel.app"
excel_file_path = "Dashboard.xlsm"


# Use the 'open' command to open the Excel file with Excel
subprocess.Popen(['open', '-a', excel_path, excel_file_path])

#push updates to GitHub for web access
subprocess.run(["git", "add", "."])
subprocess.run(["git", "commit", "-m", "Data update"])
subprocess.run(["git", "push", "origin", "main"])
