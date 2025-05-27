# AI-Powered Tech Newsletter Data Pipeline

This repository contains an Apache Airflow DAG that automatically generates and distributes a weekly AI-powered technology newsletter. The pipeline curates content from top RSS feeds, enhances it using OpenAI, renders it in HTML using Jinja2, and sends it via Amazon SES.

## Features

* Fetches articles from popular tech RSS feeds
* Cleans and categorizes content using BeautifulSoup
* Enhances summaries and editorial content with GPT-4o
* Renders branded HTML newsletters via Jinja2
* Stores raw and generated content in MongoDB
* Sends final newsletters through AWS SES
* Runs every Monday at 8 AM (UTC)

## Stack

* **Airflow 3.0+**
* **OpenAI API** (GPT-4o)
* **MongoDB Atlas**
* **Amazon SES**
* **Jinja2** for HTML templating
* **feedparser**, **BeautifulSoup** for content parsing

## Environment Variables (Airflow Variables)

| Variable           | Description                          |
| ------------------ | ------------------------------------ |
| `OPENAI_API_KEY`   | OpenAI API key                       |
| `MONGODB_USER`     | MongoDB Atlas username               |
| `MONGODB_PASSWORD` | MongoDB Atlas password               |
| `MONGODB_HOST`     | MongoDB Atlas host                   |
| `AWS_ACCESS_KEY`   | AWS access key for SES               |
| `AWS_SECRET_KEY`   | AWS secret key for SES               |
| `AWS_REGION_NAME`  | AWS region for SES                   |
| `SOURCE_EMAIL`     | Verified sender email address in SES |
| `SUBSCRIBERS`      | Comma-separated recipient email list |

## Folder Structure

```
project/
├── dags/
│   └── newsletter.py
├── templates/
│   └── newsletter.html
└── requirements.txt
```

## Usage

1. **Add required variables** to Airflow via UI or CLI.
2. **Deploy the DAG** in your Airflow environment.
3. **Customize the HTML template** in `templates/newsletter.html`.
4. **Enable the DAG** and monitor via the Airflow UI.

## Output

* **MongoDB**: Stores raw and AI-enhanced article data
* **HTML Newsletter**: Rendered with enhanced summaries
* **Email**: Sent to configured recipients via SES

## License

MIT License.
