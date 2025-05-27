import boto3
import feedparser
import json
import logging
import html
from pathlib import Path
from datetime import datetime
from airflow.sdk import dag, task, Variable
from openai import OpenAI
from jinja2 import Environment, FileSystemLoader
from pymongo import MongoClient
from bs4 import BeautifulSoup


logger = logging.getLogger(__name__)

NEWSLETTER_CONFIG = {
    "name": "Tech Weekly Digest",
    "theme": "Latest technology news and insights",
    "target_audience": "Tech professionals and enthusiasts",
    "max_articles": 8,
    "sections": {
        "top_stories": 3,
        "deep_dive": 1,
        "quick_hits": 4,
        "tools_resources": 2,
    },
}

CONTENT_SOURCES = {
    "rss_feeds": [
        "https://techcrunch.com/feed/",
        "https://www.theverge.com/rss/index.xml",
        "https://venturebeat.com/feed/",
        "https://www.wired.com/feed/rss",
        "https://www.technologyreview.com/feed/",
        "https://www.deepmind.com/blog/rss.xml",
        "https://www.unite.ai/feed/",
    ]
}

templates_path = Path(__file__).resolve().parent / "templates"
env = Environment(loader=FileSystemLoader(str(templates_path)))
template = env.get_template("newsletter.html")

TEST_JSON = "result.json"

openai_api_key = Variable.get("OPENAI_API_KEY")
client = OpenAI(api_key=openai_api_key)

MONGODB_USER = Variable.get("MONGODB_USER")
MONGODB_PASSWORD = Variable.get("MONGODB_PASSWORD")
MONGODB_HOST = Variable.get("MONGODB_HOST")

mongodb_client = MongoClient(
    f"mongodb+srv://{MONGODB_USER}:{MONGODB_PASSWORD}@{MONGODB_HOST}"
)


def call_openai_with_retry(
    messages, model="gpt-4o", response_format=None, max_retries=2
):
    """Helper function to call OpenAI with retry logic and error handling."""
    for attempt in range(max_retries):
        try:
            kwargs = {
                "model": model,
                "messages": messages,
                "temperature": 0.3,
                "max_tokens": 2000,
            }
            if response_format:
                kwargs["response_format"] = response_format

            response = client.chat.completions.create(**kwargs)
            return response.choices[0].message.content
        except Exception as e:
            logger.warning(f"OpenAI API call attempt {attempt + 1} failed: {e}")
            if attempt == max_retries - 1:
                raise
    return None


def clean_feed_item(item):
    cleaned = {}

    cleaned["title"] = item["title"]
    cleaned["summary"] = BeautifulSoup(
        html.unescape(item["summary"]), "html.parser"
    ).get_text(strip=True)

    soup = BeautifulSoup(item["full_content"], "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text_blocks = soup.stripped_strings
    cleaned["full_content"] = " ".join(text_blocks)
    cleaned["link"] = item["link"]
    cleaned["published"] = item["published"]
    cleaned["source"] = item["source"]
    cleaned["author"] = item["author"]
    cleaned["content_type"] = item["content_type"]
    cleaned["age_days"] = item["age_days"]
    cleaned["language"] = item["language"]

    return cleaned


@dag(
    schedule="0 8 * * 1",  # Every Monday at 8 AM
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["newsletter", "ai", "automation"],
    description="AI-powered tech newsletter generation and distribution",
)
def ai_tech_newsletter():
    """A DAG to generate and distribute personalized tech newsletters using AI."""

    @task()
    def fetch_rss_articles():
        """Fetch articles from configured RSS feeds."""
        rss_feeds = CONTENT_SOURCES.get("rss_feeds", [])
        all_articles = []

        for url in rss_feeds:
            try:
                feed = feedparser.parse(url)
                logger.info(f"Feed URL: {url} | Entries found: {len(feed.entries)}")

                for entry in feed.entries[:5]:
                    # Calculate article age
                    published_date = getattr(entry, "published_parsed", None)
                    article_age_days = None
                    if published_date:
                        article_date = datetime(*published_date[:6])
                        article_age_days = (datetime.now() - article_date).days

                    full_content = entry.get("content", [{}])[0].get("value", "")

                    if full_content:
                        raw_article = {
                            "title": entry.title,
                            "summary": getattr(entry, "summary", ""),
                            "link": entry.link,
                            "published": getattr(entry, "published", ""),
                            "source": feed.feed.get("title", url),
                            "content_type": "external_news",
                            "age_days": article_age_days,
                            "author": getattr(entry, "author", ""),
                            "full_content": full_content,
                            "language": feed.feed.get("language", ""),
                        }

                        cleaned_article = clean_feed_item(raw_article)
                        all_articles.append(cleaned_article)

            except Exception as e:
                logger.error(f"Error fetching from {url}: {e}")
                continue

        recent_articles = sorted(all_articles, key=lambda x: x.get("age_days", 999))[
            :10
        ]
        logger.info(f"Total recent articles fetched: {len(recent_articles)}")
        return recent_articles

    @task()
    def store_raw_feeds_to_db(all_articles: list):
        """Store fetched RSS articles to MongoDB."""
        try:
            db = mongodb_client.newsletter_ai
            collection = db.raw_feeds

            timestamp = datetime.utcnow()
            for article in all_articles:
                article["fetched_at"] = timestamp
                collection.insert_one(article)

            logger.info(f"{len(all_articles)} articles saved to MongoDB.")
        except Exception as e:
            logger.error(f"Failed to write to MongoDB: {e}")
        finally:
            mongodb_client.close()

    @task()
    def generate_newsletter_content(articles):
        """Generate enhanced newsletter content with AI-written introductions and summaries."""

        if not isinstance(articles, list):
            raise ValueError("Input must be a list of articles")

        if not articles:
            raise ValueError("Articles list cannot be empty")

        system_prompt = f"""You are a skilled tech newsletter writer. Create engaging, professional newsletter content.

    Newsletter: {NEWSLETTER_CONFIG['name']}
    Theme: {NEWSLETTER_CONFIG['theme']}
    Target: {NEWSLETTER_CONFIG['target_audience']}

    Your task is to:
    1. Write compelling introductions for each section
    2. Enhance article summaries using the full_content if available to make them more engaging
    3. Add editorial commentary where appropriate
    4. Create smooth transitions between sections
    5. Categorize articles appropriately based on their content and significance

    Article categorization guidelines:
    - Top Stories: Major news, significant product launches, industry-changing events
    - Deep Dive: Complex topics, detailed analyses, how-to guides that require more attention
    - Quick Hits: Smaller updates, tips, brief news items
    - Tools & Resources: Product reviews, useful tools, resources for professionals"""

        user_prompt = f"""Create newsletter sections from these articles. Categorize them appropriately and enhance their summaries:

    {json.dumps(articles, indent=2)}

    Return a JSON object with this structure:
    {{
        "newsletter_intro": "engaging weekly intro paragraph that sets the tone and highlights key themes",
        "sections": {{
            "top_stories": {{
                "intro": "section introduction (2-3 sentences)",
                "articles": [
                    {{
                        "title": "original article title",
                        "enhanced_summary": "rewritten engaging summary (2-3 sentences, more compelling than original)",
                        "link": "article url",
                        "source": "source name",
                        "author": "author name if available",
                        "editorial_note": "optional brief commentary or insight (1 sentence)"
                    }}
                ]
            }},
            "deep_dive": {{
                "intro": "section introduction explaining why these deserve deeper attention",
                "articles": [
                    {{
                        "title": "original article title",
                        "enhanced_summary": "detailed, engaging summary that highlights key insights",
                        "link": "article url",
                        "source": "source name",
                        "author": "author name if available",
                        "editorial_note": "editorial insight or context"
                    }}
                ]
            }},
            "quick_hits": {{
                "intro": "brief intro for rapid-fire updates",
                "articles": [
                    {{
                        "title": "original article title",
                        "enhanced_summary": "concise but engaging summary",
                        "link": "article url",
                        "source": "source name",
                        "author": "author name if available"
                    }}
                ]
            }},
            "tools_resources": {{
                "intro": "introduction to useful tools and resources",
                "articles": [
                    {{
                        "title": "original article title",
                        "enhanced_summary": "summary focused on practical value and usage",
                        "link": "article url",
                        "source": "source name",
                        "author": "author name if available",
                        "editorial_note": "why this is valuable for readers"
                    }}
                ]
            }}
        }},
        "newsletter_outro": "closing paragraph with call-to-action and preview of next edition"
    }}

    Important notes:
    - Not all sections need to have articles - only include sections that have relevant content
    - Distribute articles logically across sections based on their content and importance
    - Make enhanced summaries more engaging than the originals while staying accurate
    - Use the full_content when available to create better summaries
    - Keep editorial notes brief but insightful"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        result = call_openai_with_retry(
            messages=messages, response_format={"type": "json_object"}
        )

        logger.info("Newsletter content generation completed")
        return json.loads(result)

    @task()
    def store_generated_newsletter(newsletter_content: dict):
        """Store generated newsletter to MongoDB."""
        try:
            db = mongodb_client.newsletter_ai
            collection = db.generated_newsletters

            newsletter_content["generated_at"] = datetime.utcnow()
            collection.insert_one(newsletter_content)

            logger.info("Generated newsletter saved to MongoDB.")
        except Exception as e:
            logger.error(f"Failed to write to MongoDB: {e}")
        finally:
            mongodb_client.close()

    @task()
    def render_html(generated_newsletter_content: dict):
        """Render HTML content for newsletter using enhanced summaries."""

        current_date = datetime.now().strftime("%B %d, %Y")
        current_time = datetime.now().strftime("%I:%M %p")

        try:
            sections = generated_newsletter_content.get("sections", {})
            newsletter_intro = generated_newsletter_content.get("newsletter_intro", "")
            newsletter_outro = generated_newsletter_content.get("newsletter_outro", "")
            issue_number = generated_newsletter_content.get("issue_number", 1)

            total_articles = 0
            total_read_time = 0

            for section_key, section in sections.items():
                articles = section.get("articles", [])
                for article in articles:
                    article["summary"] = article.get("enhanced_summary", "")
                    article["read_time"] = article.get("read_time", 3)
                    article["tags"] = article.get("tags", [])
                    article["editorial_note"] = article.get("editorial_note", "")
                    article["author"] = article.get("author", "")
                    article["source_url"] = article.get("link", "#")
                    article["published_date"] = article.get("published_date", "")

                    total_read_time += article["read_time"]

                total_articles += len(articles)

            template_context = {
                "title": NEWSLETTER_CONFIG["name"],
                "date": current_date,
                "time": current_time,
                "issue_number": issue_number,
                "newsletter_intro": newsletter_intro,
                "newsletter_outro": newsletter_outro,
                "sections": sections,
                "article_count": total_articles,
                "total_read_time": total_read_time,
                "newsletter_config": NEWSLETTER_CONFIG,
                "brand_color": NEWSLETTER_CONFIG.get("brand_color", "#007bff"),
                "brand_logo": NEWSLETTER_CONFIG.get("logo_url", ""),
                "social_links": NEWSLETTER_CONFIG.get("social_links", {}),
                "unsubscribe_url": NEWSLETTER_CONFIG.get("unsubscribe_url", "#"),
                "manage_preferences_url": NEWSLETTER_CONFIG.get("preferences_url", "#"),
                "forward_url": NEWSLETTER_CONFIG.get("forward_url", "#"),
                "has_top_stories": "top_stories" in sections
                and sections["top_stories"].get("articles"),
                "has_deep_dive": "deep_dive" in sections
                and sections["deep_dive"].get("articles"),
                "has_quick_hits": "quick_hits" in sections
                and sections["quick_hits"].get("articles"),
                "has_tools_resources": "tools_resources" in sections
                and sections["tools_resources"].get("articles"),
                "format_date": lambda d: (
                    d.strftime("%B %d, %Y") if hasattr(d, "strftime") else d
                ),
                "truncate": lambda text, length=150: (
                    text[:length] + "..." if len(text) > length else text
                ),
            }

            rendered_html = template.render(**template_context)

            logger.info(
                f"HTML rendered: {total_articles} articles across {len(sections)} sections, "
                f"~{total_read_time} min read"
            )

            return rendered_html

        except Exception as e:
            logger.error(f"Template rendering error: {e}")
            raise

    @task
    def send_newsletter_via_ses(html_body: str):
        aws_access_key = Variable.get("AWS_ACCESS_KEY")
        aws_secret_key = Variable.get("AWS_SECRET_KEY")
        aws_region_name = Variable.get("AWS_REGION_NAME")

        recipients = Variable.get("SUBSCRIBERS").split(",")
        source_email = Variable.get("SOURCE_EMAIL")

        try:
            ses_client = boto3.client(
                "ses",
                region_name=aws_region_name,
                aws_access_key_id=aws_access_key,
                aws_secret_access_key=aws_secret_key,
            )

            current_date = datetime.now().strftime("%B %d, %Y")
            subject = f"{NEWSLETTER_CONFIG['name']} - {current_date}"

            response = ses_client.send_email(
                Source=source_email,
                Destination={"ToAddresses": recipients},
                Message={
                    "Subject": {"Data": subject},
                    "Body": {"Html": {"Data": html_body, "Charset": "UTF-8"}},
                },
            )

            return {
                "status": "success",
                "message_id": response["MessageId"],
                "recipients": recipients,
            }

        except Exception as e:
            logger.error(f"Error sending emails: {e}")
            raise

    # Define the task flow
    articles = fetch_rss_articles()
    store_raw_feeds_to_db(all_articles=articles)
    newsletter_content = generate_newsletter_content(articles=articles)
    store_generated_newsletter(newsletter_content=newsletter_content)
    html_outputs = render_html(generated_newsletter_content=newsletter_content)
    send_newsletter_via_ses(html_body=html_outputs)

    return html_outputs


newsletter_dag = ai_tech_newsletter()
