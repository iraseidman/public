from bs4 import BeautifulSoup
import os
import pandas as pd
import requests
import feedparser
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
import subprocess
import json
import sys

print("Dashboard script running on GitHub Actions...")

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

# FUNCTIONS
STOCKS = {
    "CHIPOTLE MEXICAN GRILL, INC.": "CMG",
    "GODADDY INC.": "GDDY",
    "American Funds 2060 Target Date Retirement Fd;R6":
    "RFUTX",  # mutual fund ticker
    "Amplify Etho Clm Ld US": "ETHO",
    "DELTA AIR LINES, INC.": "DAL",
    "THE HOME DEPOT, INC.": "HD",
}

# Fetches
Weather_API_KEY = "63effaa321b60aefc5c1084a669ca7ae"

def fetch_weather(city: str, country_code: str, output_file: str):
    """
    Fetch 5-day / 3-hour forecast from OpenWeather and save to CSV.
    """

    # API endpoint
    url = "https://api.openweathermap.org/data/2.5/forecast"

    params = {
        "q": f"{city},{country_code}",
        "appid": Weather_API_KEY,
        "units": "metric"  # use "imperial" for Fahrenheit
    }

    response = requests.get(url, params=params)

    if response.status_code != 200:
        print(f"Failed to retrieve weather for {city}: {response.text}")
        return

    data = response.json()

    rows = []

    for entry in data["list"]:
        dt = datetime.datetime.fromtimestamp(entry["dt"])

        rows.append({
            "Datetime": dt.strftime("%Y-%m-%d %H:%M"),
            "Temp (°C)": entry["main"]["temp"],
            "Feels Like (°C)": entry["main"]["feels_like"],
            "Min Temp (°C)": entry["main"]["temp_min"],
            "Max Temp (°C)": entry["main"]["temp_max"],
            "Humidity (%)": entry["main"]["humidity"],
            "Weather": entry["weather"][0]["description"],
            "Wind Speed (m/s)": entry["wind"]["speed"],
            "Clouds (%)": entry["clouds"]["all"]
        })

    df = pd.DataFrame(rows)
    df.to_csv(output_file, index=False)

    # Upper case for print statements
    if city == "utrecht":
        city = "Utrecht"
    else:
        city ="New York"

    print(f"{city} weather saved to Weather folder")

def fetch_stocks(symbols: list[str], output_file: str):
    """Fetch detailed stock data from Yahoo Finance and save to CSV."""
    try:
        rows = []

        for symbol in symbols:
            try:
                stock = yf.Ticker(symbol)
                info = stock.info
                hist = stock.history(period="5d")

                if hist.empty:
                    continue

                current_price = hist['Close'].iloc[-1]
                prev_close = info.get(
                    'previousClose',
                    hist['Close'].iloc[-2] if len(hist) > 1 else current_price
                )

                price_change = current_price - prev_close
                percent_change = (
                    price_change / prev_close * 100
                    if prev_close != 0 else 0
                )

                rows.append({
                    "Ticker": symbol,
                    "Company": info.get('longName', symbol),
                    "Current Price": round(current_price, 2),
                    "Price Change": round(price_change, 2),
                    "Percent Change": round(percent_change, 2),
                    "52 Week High": info.get('fiftyTwoWeekHigh'),
                    "52 Week Low": info.get('fiftyTwoWeekLow'),
                    "Market Cap": info.get('marketCap'),
                    "Volume": hist['Volume'].iloc[-1]
                })

            except Exception as e:
                rows.append({
                    "Ticker": symbol,
                    "Error": str(e)
                })

        df = pd.DataFrame(rows)
        df.to_csv(output_file, index=False)

        print("Stock data saved to Stocks folder")

    except Exception as e:
        print(f"Error fetching stock data: {str(e)}")

# Save
def save_email_data(email_data, filepath: str):
    df = pd.DataFrame(email_data)
    df.to_csv(filepath, index=False, quoting=csv.QUOTE_ALL)

def convert_to_utc_iso(date_str):
    """Convert email date to ISO 8601 UTC format (for frontend local time conversion)"""
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

def scrape_yahoo(username: str, password: str, limit: int = 10) -> list[dict]:
    """Fetch recent emails from Yahoo via IMAP."""
    imap_server = "imap.mail.yahoo.com"
    mail = imaplib.IMAP4_SSL(imap_server)

    try:
        mail.login(username, password)
        mail.select("inbox")

        # Search for all messages
        status, messages = mail.search(None, "ALL")
        if status != "OK":
            return []

        msg_nums = messages[0].split()[-limit:]  # last N messages
        results = []

        for num in reversed(msg_nums):
            status, msg_data = mail.fetch(num, "(RFC822)")
            if status != "OK":
                continue

            raw_msg = msg_data[0][1]
            msg = email.message_from_bytes(raw_msg)

            subject, encoding = decode_header(msg["Subject"])[0]
            if isinstance(subject, bytes):
                subject = subject.decode(encoding or "utf-8", errors="ignore")

            from_ = msg.get("From", "")
            date_ = msg.get("Date", "")

            results.append({"from": from_, "subject": subject, "date": date_})

        return results
    finally:
        mail.logout()

def git_commit_and_push(commit_message: str):
    try:
        # Stage all changes
        subprocess.run(
            ["git", "add", "."],
            check=True
        )

        # Commit (will fail gracefully if no changes)
        commit_result = subprocess.run(
            ["git", "commit", "-m", commit_message],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        if "nothing to commit" in commit_result.stdout.lower():
            print("No Git changes to commit.")
            return

        # Push to origin (default branch)
        subprocess.run(
            ["git", "-C", ".", "push"],
            check=True
        )

        print("Git commit and push successful.")

    except subprocess.CalledProcessError as e:
        print("Git operation failed:")
        print(e.stderr)

# Create folders if they don't exist
os.makedirs("Cartoon", exist_ok=True)
os.makedirs("Weather", exist_ok=True)
os.makedirs("Email", exist_ok=True)
os.makedirs("Stocks", exist_ok=True)

# CARTOON PULL
session = requests.Session()  #used for web-scraping fetches throughout the entire script

# Send an HTTP GET request to the URL
cartoon_url = "https://www.newyorker.com/"
response = session.get(cartoon_url)

if response.status_code != 200:
    raise RuntimeError("Failed to load New Yorker cartoon page")

soup = BeautifulSoup(response.text, "html.parser")

# Find the cartoon image robustly
picture = soup.select_one(
    "picture.ResponsiveImagePicture-jKunQM.gjCCFj.ResponsiveCartoonImage-gvCZEW.hlHoEM.responsive-cartoon__image.responsive-image"
)

if not picture:
    raise RuntimeError("Cartoon picture not found")

img = picture.find("img")
if not img:
    raise RuntimeError("Cartoon img tag not found")

image_url = (
    img.get("src")
    or img.get("data-src")
    or img.get("data-lazy-src")
)

if not image_url:
    raise RuntimeError("No image URL found")

# Handle relative URLs
if image_url.startswith("/"):
    image_url = "https://www.newyorker.com" + image_url

# Download
image = session.get(image_url)
image.raise_for_status()

with open(os.path.join("Cartoon", "daily_cartoon.png"), "wb") as f:
    f.write(image.content)

# Caption
img = picture.find("img")
caption = img.get("alt", "No caption found")

if not caption:
    raise RuntimeError("Cartoon link not found")

# Download
with open(os.path.join("Cartoon", "captionText.txt"), "w", encoding="utf-8") as f:
    f.write(caption)

# WEATHER PULLS
fetch_weather(
    "new york", "usa",
    os.path.join("Weather", "extended_weather_forecast_nyc.csv"))
fetch_weather(
    "utrecht", "netherlands",
    os.path.join("Weather", "extended_weather_forecast_utrecht.csv"))

# FETCH Yahoo Mail
try:
    yahoo_email = os.getenv('YAHOO_EMAIL')
    yahoo_password = os.getenv('YAHOO_PASSWORD')

    if yahoo_email and yahoo_password:
        yahoo_data = scrape_yahoo(yahoo_email, yahoo_password)
        yahoo_filename = os.path.join("Email", "yahoo.csv")
        save_email_data(yahoo_data, yahoo_filename)
        print("Yahoo emails saved to Email folder")
    else:
        print("Yahoo credentials not set - skipping Yahoo data")
except Exception as e:
    print(f"Yahoo email access failed - skipping Yahoo data: {e}")

# FETCH STOCKS
# Define stock symbols to track
stock_symbols = ["CMG", "GDDY", "ETHO", "RFUTX", "DAL", "HD"]
stocks_filename = os.path.join("Stocks", "stocks.csv")
fetch_stocks(stock_symbols, stocks_filename)

session.close()  #finish web-scarping so close the session

# Push updates to GitHub
# git_commit_and_push("GitHub Actions: Update dashboard data")

print("Dashboard update complete!")
